package gosim

// Operand computation and instruction formatting, mirroring pi32v2isa.codec
// and pi32v2isa.check.operands exactly (the pinned upstream semantics).

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const M32 = 0xFFFFFFFF

// srNames mirrors codec.SR_NAMES.
var srNames = []string{
	"reti", "rete", "retx", "rets", "sr4", "psr", "cnum", "uspn",
	"sspn", "icfgn", "icfgs", "icfg", "usp", "ssp", "sp", "pc",
}

func modimm(raw uint32) uint32 {
	if raw>>10 == 0 {
		xy := raw & 0xFF
		switch (raw >> 8) & 3 {
		case 0:
			return xy
		case 1:
			return xy * 0x00010001
		case 2:
			return xy * 0x01000100
		default:
			return xy * 0x01010101
		}
	}
	rot := raw >> 7
	b := 0x80 | (raw & 0x7F)
	return ((b >> rot) | (b << (32 - rot))) & 0xFFFFFFFF
}

// fieldValue mirrors codec.value: simm sign-extension, zero_as, scale/add.
// Returns a signed 64-bit value; for simm the result may be negative.
func fieldValue(kind string, raw uint32, width int, fm *fieldMeta) int64 {
	r := int64(raw)
	if kind == "simm" && raw >= 1<<(width-1) {
		r -= int64(1) << width
	}
	if raw == 0 && fm.zeroAs >= 0 {
		return int64(fm.zeroAs)
	}
	return r*int64(fm.scale) + int64(fm.add)
}

// runs mirrors codec._runs: regs in descending order, runs of >=3 as ranges.
func runs(regs []int) string {
	var parts []string
	k := 0
	for k < len(regs) {
		j := k
		for j+1 < len(regs) && regs[j+1] == regs[j]-1 {
			j++
		}
		if j-k >= 2 {
			parts = append(parts, fmt.Sprintf("r%d-r%d", regs[k], regs[j]))
		} else {
			for _, r := range regs[k : j+1] {
				parts = append(parts, fmt.Sprintf("r%d", r))
			}
		}
		k = j + 1
	}
	return strings.Join(parts, ", ")
}

// operand mirrors codec.operand (the assembler text of one field).
func operand(f *fieldMeta, raw uint32, width int) string {
	switch f.kind {
	case "gpr", "gpr_lo":
		return fmt.Sprintf("r%d", raw)
	case "gpr_hi":
		return fmt.Sprintf("r%d", raw+8)
	case "pair":
		return fmt.Sprintf("r%d_r%d", 2*raw+1, 2*raw)
	case "pair_regs":
		return fmt.Sprintf("r%d,r%d", 2*(raw>>2)+(raw>>1&1), 2*(raw>>2)+(raw&1))
	case "sr":
		return srNames[raw]
	case "bit":
		return strconv.FormatUint(uint64(uint32(1)<<raw), 10)
	case "invbit":
		return strconv.FormatUint(uint64(uint32(0xFFFFFFFF)^(uint32(1)<<raw)), 10)
	case "rlist4":
		var hi, lo uint32
		if raw < 4 {
			hi, lo = 3, raw
		} else {
			hi, lo = raw, 4
		}
		regs := make([]int, 0, hi-lo+1)
		for r := int(hi); r >= int(lo); r-- {
			regs = append(regs, r)
		}
		return runs(regs)
	case "rmask":
		var regs []int
		for r := 15; r >= 0; r-- {
			if raw>>r&1 != 0 {
				regs = append(regs, r)
			}
		}
		s := runs(regs)
		if f.lead != "" {
			if s != "" {
				return f.lead + ", " + s
			}
			return f.lead
		}
		return s
	case "modimm":
		v := modimm(raw)
		if f.invert {
			v ^= 0xFFFFFFFF
		}
		if f.decimal {
			return strconv.FormatInt(int64(int32(v)), 10)
		}
		return fmt.Sprintf("0x%X", v)
	case "enum":
		return f.enums[raw]
	case "srmask":
		var parts []string
		for r := width - 1; r >= 0; r-- {
			if raw>>r&1 != 0 {
				parts = append(parts, srNames[r])
			}
		}
		return strings.Join(parts, ", ")
	default:
		return strconv.FormatInt(fieldValue(f.kind, raw, width, f), 10)
	}
}

// syntaxToken mirrors table.SYNTAX_TOKEN: "{{" | "}}" | "{+?name}".
var syntaxToken = regexp.MustCompile(`\{\{|\}\}|\{(\+?)([a-z])\}`)

// format mirrors codec.format.
func format(im *insnMeta, raw map[string]uint32) string {
	byName := make(map[string]*fieldMeta, len(im.fields))
	for i := range im.fields {
		byName[im.fields[i].name] = &im.fields[i]
	}
	return syntaxToken.ReplaceAllStringFunc(im.syntax, func(m string) string {
		if m == "{{" || m == "}}" {
			return m[:1]
		}
		sub := syntaxToken.FindStringSubmatch(m)
		plus, name := sub[1], sub[2]
		f := byName[name]
		text := operand(f, raw[name], f.width)
		if plus != "" {
			if text == "0" {
				return ""
			}
			return "+" + text
		}
		return text
	})
}

// placeholders mirrors table.placeholders: field names a syntax uses, in order.
func placeholders(syntax string) []string {
	var out []string
	for _, m := range syntaxToken.FindAllStringSubmatch(syntax, -1) {
		if m[2] != "" {
			out = append(out, m[2])
		}
	}
	return out
}

// Anchored at the start: Python's re.match semantics (semantics.v3.half_mask).
var halfMaskRe = regexp.MustCompile(`^r\d+\.([hl]) `)

// halfMask mirrors semantics.v3.half_mask.
func halfMask(text string) (uint32, bool) {
	m := halfMaskRe.FindStringSubmatch(text)
	if m == nil {
		return 0, false
	}
	if m[1] == "h" {
		return 0xFFFF0000, true
	}
	return 0x0000FFFF, true
}
