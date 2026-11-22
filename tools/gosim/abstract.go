package gosim

// Abstract-value read analysis, the Go twin of tools/gosim_observe.py's
// read_field_names / _field_regs / consumed_regs / reads_sp and of probe_boot's
// per-step "does this instruction consume an abstract value" gate. It decides
// which GPR operands an instruction READS, so the dual loop can run a scalar
// instruction while unrelated registers carry UnknownWord / IcfgValue tags, and
// hand the step back to Python (BatchAbstract) the moment a tagged value is
// actually consumed as a numeric operand, address, branch condition or control
// target.
//
// The port mirrors the Python helpers exactly:
//   * a register-kind field is a read unless it is a pure write destination of
//     a top-level '=' assignment (a compound '+='/'*='/... destination is
//     read-modify-write and stays a read, as do address bases, index registers,
//     branch conditions and store sources);
//   * sp is consumed by the Python probe_boot unknown-core-stack-pointer rule
//     (push/pop/add.sp/add.r_sp prefixes, or any '.sp' in the id except
//     mov.sp_ssp), tracked separately from the R side table via Core.SPKnown.

import "strings"

// isRegKind reports whether a field kind names one or two general registers.
// Mirrors gosim_observe._REG_KINDS.
func isRegKind(kind string) bool {
	switch kind {
	case "gpr", "gpr_lo", "gpr_hi", "pair", "pair_regs":
		return true
	}
	return false
}

// fieldByName returns the field with the given name, or nil.
func fieldByName(im *insnMeta, name string) *fieldMeta {
	for i := range im.fields {
		if im.fields[i].name == name {
			return &im.fields[i]
		}
	}
	return nil
}

// findAssign mirrors gosim_observe._ASSIGN_RE.search: the leftmost top-level
// assignment operator — a compound '+='/'-='/'*='/'&='/'|='/'^='/'<<='/'>>='
// or a plain '=' that is not part of '==', '!=', '<=' or '>='. A plain '=' is
// found left-to-right first (it is the leftmost in every instruction syntax),
// then only an even earlier compound operator can beat it.
func findAssign(syntax string) (op string, idx int, ok bool) {
	best := -1
	bestOp := ""
	// plain '=' (leftmost, with lookbehind/lookahead guards).
	for i := 0; i < len(syntax); i++ {
		if syntax[i] != '=' {
			continue
		}
		if i+1 < len(syntax) && syntax[i+1] == '=' { // part of '=='
			continue
		}
		if i > 0 {
			switch syntax[i-1] {
			case '<', '>', '=', '!': // part of '<=','>=','!=','=='
				continue
			}
		}
		best, bestOp = i, "="
		break
	}
	// compound operators: only an even earlier position wins.
	for _, comp := range []string{"<<=", ">>=", "+=", "-=", "*=", "&=", "|=", "^="} {
		for i := 0; i < len(syntax); i++ {
			if strings.HasPrefix(syntax[i:], comp) {
				if best < 0 || i < best {
					best, bestOp = i, comp
				}
				break
			}
		}
	}
	if best < 0 {
		return "", 0, false
	}
	return bestOp, best, true
}

// lhsDestFields mirrors gosim_observe._lhs_dest_fields: the register-kind field
// NAMES that are pure write destinations in the left-hand side of a top-level
// assignment (bracket depth 0; '{+n}' offsets and '{{...}}' register lists are
// not register operands).
func lhsDestFields(im *insnMeta, lhs string) map[string]bool {
	dests := map[string]bool{}
	depth := 0
	i := 0
	n := len(lhs)
	for i < n {
		ch := lhs[i]
		if ch == '{' {
			rest := lhs[i:]
			j := strings.IndexByte(rest, '}')
			if j < 0 {
				break
			}
			inner := rest[1:j]
			if inner != "{" && inner != "}" {
				name := strings.TrimLeft(inner, "+")
				if f := fieldByName(im, name); f != nil && isRegKind(f.kind) && depth == 0 {
					dests[name] = true
				}
			}
			i += j + 1
			continue
		}
		if ch == '[' {
			depth++
		} else if ch == ']' {
			depth--
		}
		i++
	}
	return dests
}

// readFieldNames mirrors gosim_observe.read_field_names: the field names an
// instruction READS as register operands (static per instruction id).
func readFieldNames(im *insnMeta) map[string]bool {
	reads := map[string]bool{}
	for i := range im.fields {
		if isRegKind(im.fields[i].kind) {
			reads[im.fields[i].name] = true
		}
	}
	op, idx, ok := findAssign(im.syntax)
	if !ok {
		return reads
	}
	if op == "=" {
		for name := range lhsDestFields(im, im.syntax[:idx]) {
			delete(reads, name)
		}
	}
	return reads
}

// fieldRegs mirrors gosim_observe._field_regs: the register indices one
// register-kind operand field names.
func fieldRegs(im *insnMeta, raw map[string]uint32, name string) []int {
	f := fieldByName(im, name)
	if f == nil {
		return nil
	}
	r := raw[name]
	switch f.kind {
	case "gpr", "gpr_lo":
		return []int{int(r)}
	case "gpr_hi":
		return []int{int(r) + 8}
	case "pair":
		return []int{2 * int(r), 2*int(r) + 1}
	case "pair_regs":
		return []int{2*(int(r)>>2) + int((r>>1)&1), 2*(int(r)>>2) + int(r&1)}
	}
	return nil
}

// consumedRegs mirrors gosim_observe.consumed_regs: the GPR indices the
// instruction READS.
func consumedRegs(im *insnMeta, raw map[string]uint32) map[int]bool {
	out := map[int]bool{}
	for name := range readFieldNames(im) {
		for _, r := range fieldRegs(im, raw, name) {
			out[r] = true
		}
	}
	return out
}

// consumesSP mirrors probe_boot's unknown-core-stack-pointer gate: whether the
// instruction consumes the (separately tracked) stack pointer.
func consumesSP(im *insnMeta) bool {
	id := im.id
	if strings.HasPrefix(id, "push") || strings.HasPrefix(id, "pop") ||
		strings.HasPrefix(id, "add.sp") || strings.HasPrefix(id, "add.r_sp") {
		return true
	}
	return strings.Contains(id, ".sp") && id != "mov.sp_ssp"
}
