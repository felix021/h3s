package gosim

// Operands mirrors the dict check.operands builds: named operand values plus
// the raw field values.

type Operands struct {
	raw map[string]uint32
	num map[string]int64
	str map[string]string
	pr  map[string][2]int64
}

func newOperands(im *insnMeta, raw map[string]uint32) *Operands {
	o := &Operands{
		raw: raw,
		num: make(map[string]int64, len(im.fields)),
		str: make(map[string]string, len(im.fields)),
		pr:  make(map[string][2]int64, len(im.fields)),
	}
	for i := range im.fields {
		f := &im.fields[i]
		r := raw[f.name]
		switch f.kind {
		case "gpr", "gpr_lo":
			o.num[f.name] = int64(r)
		case "gpr_hi":
			o.num[f.name] = int64(r) + 8
		case "pair":
			o.num[f.name] = 2 * int64(r)
		case "pair_regs":
			o.pr[f.name] = [2]int64{2*int64(r>>2) + int64(r>>1&1), 2*int64(r>>2) + int64(r&1)}
		case "bit":
			o.num[f.name] = int64(uint32(1) << r)
		case "invbit":
			o.num[f.name] = int64(uint32(0xFFFFFFFF) ^ (uint32(1) << r))
		case "modimm":
			v := int64(modimm(r))
			if f.invert {
				v = int64(uint32(v) ^ 0xFFFFFFFF)
			}
			o.num[f.name] = v
		case "enum":
			o.str[f.name] = f.enums[r]
		default: // uimm, simm, sr, rlist4, rmask, srmask
			o.num[f.name] = fieldValue(f.kind, r, f.width, f)
		}
	}
	return o
}

func (o *Operands) Num(k string) int64     { return o.num[k] }
func (o *Operands) Str(k string) string    { return o.str[k] }
func (o *Operands) Pr(k string) [2]int64   { return o.pr[k] }
func (o *Operands) Raw(k string) uint32    { return o.raw[k] }
func (o *Operands) HasNum(k string) bool   { _, ok := o.num[k]; return ok }
func (o *Operands) HasStr(k string) bool   { _, ok := o.str[k]; return ok }

// uimm32 returns the operand as a 32-bit unsigned value (for immediates).
func (o *Operands) uimm32(k string) uint32 { return uint32(o.num[k]) }
