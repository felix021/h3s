 6025c84:    12 3f             	r2 = -1
 6025c86:    81 f9 0b 06       	if (r1 < 3) goto 22 ; target 0x6025ca0
 6025c8a:    c1 ff c0 65 12 00 	r1 = 1205696
 6025c90:    58 ee 1b 2a       	r2 = b[++r1=171] (u)
 6025c94:    8a 40             	b[r0+0] = r2
 6025c96:    1a 41             	r2 = b[r1+1] (u)
 6025c98:    8a 41             	b[r0+1] = r2
 6025c9a:    19 42             	r1 = b[r1+2] (u)
 6025c9c:    89 42             	b[r0+2] = r1
 6025c9e:    42 23             	r2 = 3
 6025ca0:    20 16             	r0 = r2
 6025ca2:    80 00             	rts
 6025ca4:    75 04             	[--sp] = {rets, r5, r4}
 6025ca6:    e2 9d             	sp += -12
 6025ca8:    24 16             	r4 = r2
 6025caa:    83 f9 3f 06       	if (r3 < 3) goto 126 ; target 0x6025d2c
 6025cae:    bf ea 5f 12       	call -121666 ; target 0x6008170
 6025cb2:    80 f8 3b 0a       	if (r0 != 5) goto 118 ; target 0x6025d2c
 6025cb6:    40 25             	r0 = 5
 6025cb8:    be ea d8 d7       	call -151632 ; target 0x6000c6c
 6025cbc:    00 43             	if (r0 == 0) goto 6 ; target 0x6025cc4
 6025cbe:    bf ea 7d 04       	call -128774 ; target 0x60065bc
 6025cc2:    90 54             	if (r0 != 0) goto 104 ; target 0x6025d2c
 6025cc4:    bf ea 66 96       	call -54068 ; target 0x6018994
 6025cc8:    90 51             	if (r0 != 0) goto 98 ; target 0x6025d2c
 6025cca:    60 f0 40 19       	r1 = 0x300000  #
 6025cce:    48 40             		 r0 = b[r4+0] (u)
 6025cd0:    c5 ff c0 65 12 00 	r5 = 1205696
 6025cd6:    5a ee 5b 0a       	b[++r5=171] = r0
 6025cda:    48 41             	r0 = b[r4+1] (u)
 6025cdc:    d8 41             	b[r5+1] = r0
 6025cde:    48 42             	r0 = b[r4+2] (u)
 6025ce0:    d8 42             	b[r5+2] = r0
 6025ce2:    c0 ff 6a 5d 02 06 	r0 = 100818282
 6025ce8:    80 20             	[sp] = r0
 6025cea:    40 21             	r0 = 1
 6025cec:    80 21             	[sp+4] = r0
 6025cee:    40 20             	r0 = 0
 6025cf0:    80 22             	[sp+8] = r0
 6025cf2:    c0 ff 11 1d 0a 06 	r0 = 101326097
 6025cf8:    8b 80             	r3 = sp + 0
 6025cfa:    42 23             	r2 = 3
 6025cfc:    80 ff 76 ec 03 00 	call 257142 ; target 0x6064978
 6025d02:    58 40             	r0 = b[r5+0] (u)
 6025d04:    80 f8 12 02       	if (r0 != 1) goto 36 ; target 0x6025d2c
 6025d08:    59 41             	r1 = b[r5+1] (u)
 6025d0a:    32 e1 ff 1f       	r2 = r1 + -1
 6025d0e:    c0 ff 80 4c 12 00 	r0 = 1199232
 6025d14:    02 f9 04 08       	if (r2 >= 4) goto 8 ; target 0x6025d20
 6025d18:    5a 42             	r2 = b[r5+2] (u)
 6025d1a:    89 41             	b[r0+1] = r1
 6025d1c:    8a 42             	b[r0+2] = r2
 6025d1e:    04 86             	goto 12 ; target 0x6025d2c
 6025d20:    31 e8 05 c0       	if (r1 == 5) {
 6025d24:    41 21             		r1 = 1
 6025d26:    89 41             		b[r0+1] = r1
 6025d28:    41 22             		r1 = 2
 6025d2a:    89 42             		b[r0+2] = r1
 6025d2c:    40 20             	r0 = 0
 6025d2e:    02 83             	sp += 12
 6025d30:    55 04             	{pc, r5, r4} = [sp++]
