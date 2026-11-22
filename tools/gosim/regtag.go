package gosim

// RegTag is the typed side-table entry for one GPR slot that carries an abstract
// value instead of a concrete uint32. The per-core Core.RT [16]*RegTag table is
// the lossless twin of Core.R: a nil tag means R[i] is the whole truth (a
// concrete integer); a non-nil tag means R[i] is a placeholder (0) and the
// abstract value rides here.
//
// Field names mirror world_snapshot._ABSTRACT_FIELDS:
//
//	UnknownWord: bytes (4, nil = unknown byte) + origins (4, nil = no physical
//	  provenance; a known byte always has a nil origin).
//	IcfgValue:   value / known / origin (core, status_generation) / identity.
//
// The two kinds never mix fields; the JSON encoder on the Python side only
// emits the fields for the declared kind, and decode reconstructs exactly one
// Python object.
type RegTag struct {
	Kind string `json:"kind"` // "unknown_word" | "icfg_value"

	// UnknownWord fields (kind == "unknown_word").
	Bytes   []*uint8  `json:"bytes"`   // length 4; nil = unknown byte
	Origins []*uint32 `json:"origins"` // length 4; nil = no origin

	// IcfgValue fields (kind == "icfg_value").
	Value    uint32  `json:"value"`
	Known    uint32  `json:"known"`
	Origin   *[2]int `json:"origin"` // (core, status_generation); nil unless icfg_value
	Identity uint32  `json:"identity"`
}
