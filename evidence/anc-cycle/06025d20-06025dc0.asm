 6025d20:    31 e8 05 c0       	if (r1 == 5) {
 6025d24:    41 21             		r1 = 1
 6025d26:    89 41             		b[r0+1] = r1
 6025d28:    41 22             		r1 = 2
 6025d2a:    89 42             		b[r0+2] = r1
 6025d2c:    40 20             	r0 = 0
 6025d2e:    02 83             	sp += 12
 6025d30:    55 04             	{pc, r5, r4} = [sp++]
 6025d32:    74 04             	[--sp] = {rets, r4}
 6025d34:    c4 ff c0 65 12 00 	r4 = 1205696
 6025d3a:    40 21             	r0 = 1
 6025d3c:    41 16             	r1 = r4
 6025d3e:    5a ee 1b 0a       	b[++r1=171] = r0
 6025d42:    98 41             	b[r1+1] = r0
 6025d44:    40 22             	r0 = 2
 6025d46:    98 42             	b[r1+2] = r0
 6025d48:    bf ea 37 2f       	call -106898 ; target 0x600bbba
 6025d4c:    00 42             	if (r0 == 0) goto 4 ; target 0x6025d52
 6025d4e:    bf ea a5 31       	call -105654 ; target 0x600c09c
 6025d52:    00 e1 ab 40       	r0 = r4 + 171
 6025d56:    0a 42             	r2 = b[r0+2] (u)
 6025d58:    09 41             	r1 = b[r0+1] (u)
 6025d5a:    08 40             	r0 = b[r0+0] (u)
 6025d5c:    bf ea 41 67       	call -78206 ; target 0x6012be2
 6025d60:    40 22             	r0 = 2
 6025d62:    41 20             	r1 = 0
 6025d64:    bf ea ea 3e       	call -98860 ; target 0x600db3c
 6025d68:    54 04             	{pc, r4} = [sp++]
 6025d6a:    74 04             	[--sp] = {rets, r4}
 6025d6c:    c4 ff 6b 66 12 00 	r4 = 1205867
 6025d72:    4a 42             	r2 = b[r4+2] (u)
 6025d74:    49 41             	r1 = b[r4+1] (u)
 6025d76:    48 40             	r0 = b[r4+0] (u)
 6025d78:    bf ea 33 67       	call -78234 ; target 0x6012be2
 6025d7c:    4a 42             	r2 = b[r4+2] (u)
 6025d7e:    49 41             	r1 = b[r4+1] (u)
 6025d80:    48 40             	r0 = b[r4+0] (u)
 6025d82:    43 21             	r3 = 1
 6025d84:    bf ea b3 3f       	call -98458 ; target 0x600dcee
 6025d88:    54 04             	{pc, r4} = [sp++]
 6025d8a:    10 04             	[--sp] = rets
 6025d8c:    bf ea aa 8c       	call -59052 ; target 0x60176e4
 6025d90:    90 5c             	if (r0 != 0) goto 120 ; target 0x6025e0a
 6025d92:    bf ea ff 95       	call -54274 ; target 0x6018994
 6025d96:    90 59             	if (r0 != 0) goto 114 ; target 0x6025e0a
 6025d98:    40 21             	r0 = 1
 6025d9a:    bf ea 30 32       	call -105376 ; target 0x600c1fe
 6025d9e:    bf ea 1b 2f       	call -106954 ; target 0x600bbd8
 6025da2:    00 f8 08 04       	if (r0 == 2) goto 16 ; target 0x6025db6
 6025da6:    40 22             	r0 = 2
 6025da8:    41 20             	r1 = 0
 6025daa:    bf ea c7 3e       	call -98930 ; target 0x600db3c
 6025dae:    58 24             	r0 = 100
 6025db0:    80 ff 46 f1 03 00 	call 258374 ; target 0x6064efc
 6025db6:    bf ea db 11       	call -121930 ; target 0x6008170
 6025dba:    80 f8 26 0a       	if (r0 != 5) goto 76 ; target 0x6025e0a
 6025dbe:    40 25             	r0 = 5
