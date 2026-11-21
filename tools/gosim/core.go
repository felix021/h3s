package gosim

// CPU, instruction decode, effect/apply and the reference step engine,
// mirroring the pinned upstream tools/llvm/sim.py (the pi32v2 semantic
// authority). Peripheral models, ledgers and assertions stay in Python; this
// package is the pure decode+execute core.

import (
	"errors"
	"fmt"
	"strings"
)

var (
	ErrNoInstruction  = errors.New("no instruction decodes here")
	ErrNoModel        = errors.New("has no model")
	ErrNeedsContext   = errors.New("its model needs the hardware run's context")
)

const (
	RETURN    = 0x01C70000
	RETS_MASK = 0x07FFFFFE
)

type State struct {
	sp, rets, stub, end, tgt, taken uint32
	scratch                         uint32
	batch                           string
	mem                             *flatMem
	rec                             map[string]any
}

func (st *State) load(addr uint32, size int) uint32 {
	b := st.mem.read(addr&0xFFFFFFFF, size)
	var v uint32
	for i := 0; i < size && i < 4; i++ {
		v |= uint32(b[i]) << (8 * i)
	}
	return v
}

type Effect struct {
	regs     map[int]uint32
	flags    uint32
	flagsKey bool // "flags" key present in the result dict
	flagsSet bool // "flags" value is not None (apply updates flags)
	sp       uint32
	hasSp    bool
	rets     uint32
	hasRets  bool
	exit     int
	mem      map[uint32]uint32
}

func (e *Effect) setReg(r int, v uint32) {
	if e.regs == nil {
		e.regs = make(map[int]uint32)
	}
	e.regs[r] = v & 0xFFFFFFFF
}

func (e *Effect) setMem(a, v uint32) {
	if e.mem == nil {
		e.mem = make(map[uint32]uint32)
	}
	e.mem[a&0xFFFFFFFF] = v & 0xFFFFFFFF
}

// fluent builders, so model ports read like the Python dicts they mirror.

func (e *Effect) reg(r int, v uint32) *Effect  { e.setReg(r, v); return e }
func (e *Effect) fl(v uint32) *Effect         { e.flagsKey, e.flagsSet, e.flags = true, true, v&0xF; return e }
func (e *Effect) flNone() *Effect             { e.flagsKey, e.flagsSet = true, false; return e }
func (e *Effect) spv(v uint32) *Effect        { e.hasSp, e.sp = true, v&0xFFFFFFFF; return e }
func (e *Effect) retsv(v uint32) *Effect      { e.hasRets, e.rets = true, v&0xFFFFFFFF; return e }
func (e *Effect) exitv(v int) *Effect         { e.exit = v; return e }
func (e *Effect) memw(a, v uint32) *Effect    { e.setMem(a, v); return e }

func ef() *Effect { return &Effect{} }

type modelFunc func(c *CPU, op *Operands, st *State) (*Effect, error)

type CPU struct {
	R        [16]uint32
	flags    uint32
	sp       uint32
	rets     uint32
	pc       uint32
	steps    uint64
	mem      *flatMem
	tab      []insnMeta
	models   map[string]modelFunc
	cache    map[uint32]decodeResult
	seq      uint32
	afterT   *afterThen
	icfg     uint32
	hasAfter bool
}

type afterThen struct {
	left, elseN int
}

type decodeResult struct {
	im   *insnMeta
	raw  map[string]uint32
	size int
}

func NewCPU() *CPU {
	return &CPU{
		mem:    newFlatMem(),
		tab:    decodeTable,
		cache:  make(map[uint32]decodeResult),
		icfg:   0x02050500, // as m-bootloader leaves it
		models: buildModels(),
	}
}

// NewArchCPU builds a CPU with the architectural (stripped) k2 models, as the
// H3S simulator installs via architectural_k2.
func NewArchCPU() *CPU {
	return &CPU{
		mem:    newFlatMem(),
		tab:    decodeTable,
		cache:  make(map[uint32]decodeResult),
		icfg:   0x02050500,
		models: buildArchModels(),
	}
}

// ---- accessors for the cgo boundary ---------------------------------------

func (c *CPU) MemWrite(addr uint32, data []byte) { c.mem.write(addr, data) }
func (c *CPU) MemRead(addr uint32, n int) []byte { return c.mem.read(addr, n) }
func (c *CPU) SetRegs(regs []uint32)             { copy(c.R[:], regs) }
func (c *CPU) GetRegs(regs []uint32)             { copy(regs, c.R[:]) }
func (c *CPU) Steps() uint64                     { return c.steps }

func (c *CPU) SetState(flags, sp, rets, pc uint32) {
	c.flags = flags & 0xF
	c.sp = sp
	c.rets = rets
	c.pc = pc
	c.afterT = nil
}

func (c *CPU) GetState() (flags, sp, rets, pc uint32) {
	return c.flags, c.sp, c.rets, c.pc
}

func (c *CPU) SetICFG(v uint32) { c.icfg = v }
func (c *CPU) GetICFG() uint32  { return c.icfg }

// Reset clears per-run state (memory, decode cache, step counter, pending
// if-block state) so one CPU handle can be reused across differential cases.
func (c *CPU) Reset() {
	c.R = [16]uint32{}
	c.flags = 0
	c.sp = 0
	c.rets = 0
	c.pc = 0
	c.steps = 0
	c.mem = newFlatMem()
	c.cache = make(map[uint32]decodeResult)
	c.seq = 0
	c.afterT = nil
	c.icfg = 0x02050500
}

// ErrIs is errors.Is for the core's sentinel errors.
func ErrIs(err, target error) bool {
	return errors.Is(err, target)
}

// ---- decode ---------------------------------------------------------------

func (c *CPU) decode(pc uint32) (*insnMeta, map[string]uint32, int, error) {
	if got, ok := c.cache[pc]; ok {
		return got.im, got.raw, got.size, nil
	}
	b := c.mem.read(pc, 6)
	im, raw, size, ok := decodeOne(c.tab, b)
	if !ok {
		return nil, nil, 0, fmt.Errorf("%#x: %w", pc, ErrNoInstruction)
	}
	c.cache[pc] = decodeResult{im, raw, size}
	return im, raw, size, nil
}

func decodeOne(tab []insnMeta, b []byte) (*insnMeta, map[string]uint32, int, bool) {
	w0 := uint16(b[0]) | uint16(b[1])<<8
	w1 := uint16(b[2]) | uint16(b[3])<<8
	w2 := uint16(b[4]) | uint16(b[5])<<8
	for _, i := range decodeIndex[w0] {
		im := &tab[i]
		var w uint64
		switch im.nwords {
		case 1:
			w = uint64(w0)
		case 2:
			w = uint64(w0)<<16 | uint64(w1)
		default:
			w = uint64(w0)<<32 | uint64(w1)<<16 | uint64(w2)
		}
		if w&im.mask == im.value {
			raw := make(map[string]uint32, len(im.fields))
			nbits := 16 * im.nwords
			for fi := range im.fields {
				f := &im.fields[fi]
				var v uint32
				for _, pos := range f.pos {
					v = v<<1 | uint32((w>>(nbits-1-int(pos)))&1)
				}
				raw[f.name] = v
			}
			return im, raw, 2 * im.nwords, true
		}
	}
	return nil, nil, 0, false
}

// ---- effect / apply -------------------------------------------------------

func (c *CPU) branchField(im *insnMeta) string { return im.branch }

func (c *CPU) effect(im *insnMeta, raw map[string]uint32, pc uint32, size int) (*Effect, *Operands, *uint32, error) {
	op := newOperands(im, raw)
	model := c.models[im.id]
	if model == nil {
		return nil, op, nil, fmt.Errorf("%#x: %s %w", pc, im.id, ErrNoModel)
	}
	var tgt *uint32
	if bf := im.branch; bf != "" {
		v := uint32(pc) + uint32(size) + uint32(int64(op.Num(bf)))
		tgt = &v
	}
	st := &State{
		sp:      c.sp,
		rets:    c.rets,
		stub:    pc,
		end:     pc + uint32(size),
		tgt:     tgtVal(tgt),
		taken:   0xFFFFFFFF,
		scratch: 0,
		batch:   "sim",
		mem:     c.mem,
		rec:     map[string]any{"text": ""},
	}
	res, err := model(c, op, st)
	return res, op, tgt, err
}

func tgtVal(t *uint32) uint32 {
	if t == nil {
		return 0
	}
	return *t
}

func (c *CPU) apply(res *Effect) {
	for r, v := range res.regs {
		c.R[r] = v & 0xFFFFFFFF
	}
	if res.flagsSet {
		c.flags = res.flags & 0xF
	}
	for a, v := range res.mem {
		c.mem.writeWordLE(a, v)
	}
	if res.hasSp {
		c.sp = res.sp & 0xFFFFFFFF
	}
	if res.hasRets {
		c.rets = res.rets & 0xFFFFFFFF
	}
}

func (c *CPU) memWord(a uint32) uint32 { return c.mem.wordLE(a) }

// ---- control flow ---------------------------------------------------------

func (c *CPU) skip(n int) error {
	for k := 0; k < n; k++ {
		im, _, size, err := c.decode(c.pc)
		if err != nil {
			return err
		}
		c.pc += uint32(size)
		if strings.HasSuffix(im.id, ".p") {
			_, _, s2, err := c.decode(c.pc)
			if err != nil {
				return err
			}
			c.pc += uint32(s2)
		}
	}
	return nil
}

func (c *CPU) checkThen() error {
	if c.afterT == nil {
		return nil
	}
	if c.afterT.left > 0 {
		if c.pc != c.seq {
			return fmt.Errorf("%#x: a jump out of an if block before its last instruction", c.pc)
		}
		return nil
	}
	e := c.afterT.elseN
	c.afterT = nil
	if c.pc == c.seq {
		return c.skip(e)
	}
	return nil
}

// specialList mirrors sim.CPU.special_list (push/pop of special registers).
func (c *CPU) specialList(im *insnMeta, raw map[string]uint32, size int) error {
	mask := raw["m"]
	if _, ok := raw["n"]; ok {
		mask = raw["n"]
	}
	var regs []int
	for k := 0; k < 16; k++ {
		if mask>>k&1 != 0 {
			regs = append(regs, k)
		}
	}
	if strings.HasPrefix(im.id, "push") {
		vals := map[int]uint32{3: c.rets, 5: c.flags}
		c.sp -= uint32(4 * len(regs))
		for k, r := range regs {
			if _, ok := vals[r]; !ok {
				return fmt.Errorf("%#x: push of special register %d", c.pc, r)
			}
			c.mem.writeWordLE(c.sp+uint32(4*k), vals[r])
		}
		c.pc += uint32(size)
	} else {
		a := c.sp
		newPC := c.pc + uint32(size)
		for _, r := range regs {
			v := c.memWord(a)
			a += 4
			switch r {
			case 3:
				c.rets = v & 0x07FFFFFE
			case 5:
				c.flags = v & 0xF
			case 15:
				newPC = v & 0xFFFFFFFE
			default:
				return fmt.Errorf("%#x: pop of special register %d", c.pc, r)
			}
		}
		c.sp = a
		c.pc = newPC
	}
	c.steps++
	return nil
}

func (c *CPU) step1(im *insnMeta, raw map[string]uint32, size int) error {
	if strings.HasSuffix(im.id, ".p") {
		res, bIm, _, end, err := c.pair(im, raw, size)
		if err != nil {
			return err
		}
		c.apply(res)
		if strings.HasPrefix(bIm.id, "pop.pc") {
			c.pc = c.memWord(c.sp-4) & 0xFFFFFFFE
		} else {
			c.pc = end
		}
		c.steps++
		return nil
	}
	res, op, tgt, err := c.effect(im, raw, c.pc, size)
	if err != nil {
		return err
	}
	before := c.R
	c.apply(res)
	end := c.pc + uint32(size)
	id := im.id
	switch {
	case id == "rts":
		c.pc = c.rets
	case strings.HasPrefix(id, "pop.pc"):
		c.pc = c.memWord(c.sp-4) & 0xFFFFFFFE
	case id == "call.r" || id == "goto.r":
		c.pc = before[op.Num("x")]
	case tgt != nil:
		if res.exit != 0 {
			c.pc = *tgt
		} else {
			c.pc = end
		}
	default:
		c.pc = end
	}
	c.steps++
	return nil
}

// pair mirrors sim.CPU.pair: "A #" then B (B reads registers as before A).
func (c *CPU) pair(insn *insnMeta, raw map[string]uint32, size int) (*Effect, *insnMeta, *Operands, uint32, error) {
	var base *insnMeta
	if strings.HasSuffix(insn.id, ".p") {
		base = c.tabByID(strings.TrimSuffix(insn.id, ".p"))
	} else {
		base = insn
	}
	braw := raw
	if base != insn {
		tp := placeholders(insn.syntax)
		bp := placeholders(base.syntax)
		braw = make(map[string]uint32, len(base.fields))
		for i := range bp {
			if i < len(tp) {
				braw[bp[i]] = raw[tp[i]]
			}
		}
		for _, f := range base.fields {
			if _, ok := braw[f.name]; !ok {
				if v, ok := raw[f.name]; ok {
					braw[f.name] = v
				}
			}
		}
	}
	aRes, _, _, err := c.effect(base, braw, c.pc, size)
	if err != nil {
		return nil, nil, nil, 0, err
	}
	bIm, bRaw, bSize, err := c.decode(c.pc + uint32(size))
	if err != nil {
		return nil, nil, nil, 0, err
	}
	bRes, bOp, _, err := c.effect(bIm, bRaw, c.pc+uint32(size), bSize)
	if err != nil {
		return nil, nil, nil, 0, err
	}
	regs := bRes.regs
	if regs == nil {
		regs = map[int]uint32{}
	}
	mask, hasMask := halfMask(format(insn, raw))
	for r, v := range aRes.regs {
		if hasMask {
			if bv, ok := regs[r]; ok {
				v = (bv & ^mask) | (v & mask)
			}
		}
		regs[r] = v
	}
	res := &Effect{
		regs:    regs,
		mem:     bRes.mem,
		sp:      bRes.sp,
		hasSp:   bRes.hasSp,
		rets:    bRes.rets,
		hasRets: bRes.hasRets,
		exit:    bRes.exit,
	}
	// res["flags"] = a_res.get("flags", b_res.get("flags"))
	if aRes.flagsKey {
		res.flagsKey, res.flagsSet, res.flags = true, aRes.flagsSet, aRes.flags
	} else {
		res.flagsKey, res.flagsSet, res.flags = bRes.flagsKey, bRes.flagsSet, bRes.flags
	}
	return res, bIm, bOp, c.pc + uint32(size) + uint32(bSize), nil
}

func (c *CPU) tabByID(id string) *insnMeta {
	for i := range c.tab {
		if c.tab[i].id == id {
			return &c.tab[i]
		}
	}
	return nil
}

// Step executes one instruction, mirroring sim.CPU.step.
func (c *CPU) Step() error {
	im, raw, size, err := c.decode(c.pc)
	if err != nil {
		return err
	}
	c.seq = c.pc + uint32(size)
	if strings.HasSuffix(im.id, ".p") {
		_, _, s2, err := c.decode(c.seq)
		if err != nil {
			return err
		}
		c.seq += uint32(s2)
	}
	if c.afterT != nil {
		c.afterT.left--
		if (strings.HasPrefix(im.id, "call") || strings.HasPrefix(im.id, "callns")) &&
			(c.afterT.left > 0 || c.afterT.elseN > 0) {
			return fmt.Errorf("%#x: a call before the end of an if block", c.pc)
		}
	}
	if im.blockThen != "" {
		op := newOperands(im, raw)
		res, _, _, err := c.effect(im, raw, c.pc, size)
		if err != nil {
			return err
		}
		taken := res.regs[8] == 0x100
		c.pc += uint32(size)
		c.steps++
		if taken {
			c.afterT = &afterThen{left: int(op.Num(im.blockThen)), elseN: int(op.Num(im.blockElse))}
		} else {
			if err := c.skip(int(op.Num(im.blockThen))); err != nil {
				return err
			}
		}
		return c.checkThen()
	}
	if im.id == "tbb" || im.id == "tbh" {
		op := newOperands(im, raw)
		end := c.pc + uint32(size)
		w := 1
		if im.id == "tbh" {
			w = 2
		}
		b := c.mem.read(end+c.R[op.Num("x")], w)
		entry := uint32(b[0])
		if w == 2 {
			entry |= uint32(b[1]) << 8
		}
		c.pc = end + 2*entry
		c.steps++
		return nil
	}
	if im.id == "cli" || im.id == "sti" || im.id == "cli.r" || im.id == "sti.r" {
		op := newOperands(im, raw)
		if im.id == "cli.r" {
			c.R[op.Num("x")] = c.icfg
		}
		switch {
		case strings.HasPrefix(im.id, "cli"):
			c.icfg &= ^uint32(0x200)
		case im.id == "sti":
			c.icfg |= 0x200
		default:
			c.icfg = (c.icfg & ^uint32(0x200)) | (c.R[op.Num("x")] & 0x200)
		}
		c.pc += uint32(size)
		c.steps++
		return c.checkThen()
	}
	if im.id == "lockset" || im.id == "lockclr" {
		c.pc += uint32(size)
		c.steps++
		return c.checkThen()
	}
	if im.id == "push.srl" || im.id == "pop.srl" || im.id == "push.srmask" || im.id == "pop.srmask" {
		if err := c.specialList(im, raw, size); err != nil {
			return err
		}
		return c.checkThen()
	}
	if err := c.step1(im, raw, size); err != nil {
		return err
	}
	return c.checkThen()
}
