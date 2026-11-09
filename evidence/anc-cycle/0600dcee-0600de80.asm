 600dcee:    77 04             	[--sp] = {rets, r7-r4}
 600dcf0:    e2 9d             	sp += -12
 600dcf2:    34 16             	r4 = r3
 600dcf4:    26 16             	r6 = r2
 600dcf6:    17 16             	r7 = r1
 600dcf8:    00 f8 22 06       	if (r0 == 3) goto 68 ; target 0x600dd40
 600dcfc:    00 f8 2b 04       	if (r0 == 2) goto 86 ; target 0x600dd56
 600dd00:    80 f8 5a 02       	if (r0 != 1) goto 180 ; target 0x600ddb8
 600dd04:    bf ea 68 ef       	call -8496 ; target 0x600bbd8
 600dd08:    05 16             	r5 = r0
 600dd0a:    bf ea 70 ef       	call -8480 ; target 0x600bbee
 600dd0e:    00 43             	if (r0 == 0) goto 6 ; target 0x600dd16
 600dd10:    40 20             	r0 = 0
 600dd12:    bf ea be f1       	call -7300 ; target 0x600c092
 600dd16:    bf ea c1 f1       	call -7294 ; target 0x600c09c
 600dd1a:    32 e1 ff 7f       	r2 = r7 + -1
 600dd1e:    02 fc 41 08       	if (r2 > 4) goto 130 ; target 0x600dda4
 600dd22:    c0 ff 11 1d 0a 06 	r0 = 101326097
 600dd28:    c1 ff c0 65 12 00 	r1 = 1205696
 600dd2e:    02 01             	tbb [r2]
 600dd30:    03 1c             	r3 = r0 + r0
 600dd32:    26 30             	r6 = [sp+192]
 600dd34:    46 00              <unknown instruction>
 600dd36:    00 e1 9b 04       	r0 = r0 + 1179
 600dd3a:    d8 ec 0a 06       	r0 = [r0+r6<<2]
 600dd3e:    14 8b             	goto 86 ; target 0x600dd96
 600dd40:    bf ea 4a ef       	call -8556 ; target 0x600bbd8
 600dd44:    00 f8 38 06       	if (r0 == 3) goto 112 ; target 0x600ddb8
 600dd48:    40 20             	r0 = 0
 600dd4a:    61 89             	call -110 ; target 0x600dcde
 600dd4c:    40 21             	r0 = 1
 600dd4e:    bf ea a1 f2       	call -6846 ; target 0x600c294
 600dd52:    40 23             	r0 = 3
 600dd54:    04 85             	goto 10 ; target 0x600dd60
 600dd56:    bf ea 3f ef       	call -8578 ; target 0x600bbd8
 600dd5a:    00 f8 2d 02       	if (r0 == 1) goto 90 ; target 0x600ddb8
 600dd5e:    40 21             	r0 = 1
 600dd60:    41 16             	r1 = r4
 600dd62:    bf ea eb fe       	call -554 ; target 0x600db3c
 600dd66:    14 88             	goto 80 ; target 0x600ddb8
 600dd68:    00 e1 9b 04       	r0 = r0 + 1179
 600dd6c:    d8 ec 0a 06       	r0 = [r0+r6<<2]
 600dd70:    d1 ec 15 0d       	[r1+468] = r0
 600dd74:    d1 ec 14 0d       	r0 = [r1+468]
 600dd78:    40 22             	r0 = 2
 600dd7a:    04 92             	goto 36 ; target 0x600dda0
 600dd7c:    00 e1 9b 04       	r0 = r0 + 1179
 600dd80:    d8 ec 0a 06       	r0 = [r0+r6<<2]
 600dd84:    d1 ec 15 0d       	[r1+468] = r0
 600dd88:    d1 ec 14 0d       	r0 = [r1+468]
 600dd8c:    40 23             	r0 = 3
 600dd8e:    04 88             	goto 16 ; target 0x600dda0
 600dd90:    c0 ff 00 00 c6 c2 	r0 = 3267756032
 600dd96:    d1 ec 15 0d       	[r1+468] = r0
 600dd9a:    d1 ec 14 0d       	r0 = [r1+468]
 600dd9e:    40 21             	r0 = 1
 600dda0:    bf ea 78 f2       	call -6928 ; target 0x600c294
 600dda4:    b5 e8 02 80       	if (r5 != 2) {
 600dda8:    40 22             		r0 = 2
 600ddaa:    41 16             		r1 = r4
 600ddac:    bf ea c6 fe       		call -628 ; target 0x600db3c
 600ddb0:    48 28             	r0 = 40
 600ddb2:    80 ff 44 71 05 00 	call 356676 ; target 0x6064efc
 600ddb8:    02 83             	sp += 12
 600ddba:    57 04             	{pc, r7-r4} = [sp++]
 600ddbc:    c1 ff 8a 5d 02 06 	r1 = 100818314
 600ddc2:    81 20             	[sp] = r1
 600ddc4:    41 21             	r1 = 1
 600ddc6:    81 21             	[sp+4] = r1
 600ddc8:    41 20             	r1 = 0
 600ddca:    60 f0 40 19       	r1 = 0x300000  #
 600ddce:    81 22             		 [sp+8] = r1
 600ddd0:    8b 80             	r3 = sp + 0
 600ddd2:    42 23             	r2 = 3
 600ddd4:    80 ff 9e 6b 05 00 	call 355230 ; target 0x6064978
 600ddda:    f7 8e             	goto -36 ; target 0x600ddb8
 600dddc:    75 04             	[--sp] = {rets, r5, r4}
 600ddde:    c5 ff c0 65 12 00 	r5 = 1205696
 600dde4:    01 e1 ab 50       	r1 = r5 + 171
 600dde8:    40 30             	r0 = 16
 600ddea:    42 23             	r2 = 3
 600ddec:    bf ea cc 98       	call -52840 ; target 0x6000f88
 600ddf0:    c1 ff 80 4c 12 00 	r1 = 1199232
 600ddf6:    80 f8 0f 06       	if (r0 != 3) goto 30 ; target 0x600de18
 600ddfa:    50 16             	r0 = r5
 600ddfc:    58 ee 0b 3a       	r3 = b[++r0=171] (u)
 600de00:    0a 41             	r2 = b[r0+1] (u)
 600de02:    08 42             	r0 = b[r0+2] (u)
 600de04:    9a 41             	b[r1+1] = r2
 600de06:    98 42             	b[r1+2] = r0
 600de08:    03 f8 16 06       	if (r3 == 3) goto 44 ; target 0x600de38
 600de0c:    83 f8 0d 04       	if (r3 != 2) goto 26 ; target 0x600de2a
 600de10:    40 20             	r0 = 0
 600de12:    52 ee 5c 0a       	b[r5+172] = r0
 600de16:    04 94             	goto 40 ; target 0x600de40
 600de18:    42 21             	r2 = 1
 600de1a:    53 16             	r3 = r5
 600de1c:    5a ee 3b 2a       	b[++r3=171] = r2
 600de20:    ba 41             	b[r3+1] = r2
 600de22:    40 22             	r0 = 2
 600de24:    b8 42             	b[r3+2] = r0
 600de26:    9a 41             	b[r1+1] = r2
 600de28:    98 42             	b[r1+2] = r0
 600de2a:    43 21             	r3 = 1
 600de2c:    b2 e8 05 00       	if (r2 != 5) {
 600de30:    23 16             		r3 = r2
 600de32:    9b 41             	b[r1+1] = r3
 600de34:    98 42             	b[r1+2] = r0
 600de36:    04 86             	goto 12 ; target 0x600de44
 600de38:    40 21             	r0 = 1
 600de3a:    52 ee 5c 0a       	b[r5+172] = r0
 600de3e:    40 24             	r0 = 4
 600de40:    52 ee 5d 0a       	b[r5+173] = r0
 600de44:    bf ea b9 ee       	call -8846 ; target 0x600bbba
 600de48:    50 ee 5b 4a       	r4 = b[r5+171] (u)
 600de4c:    00 47             	if (r0 == 0) goto 14 ; target 0x600de5c
 600de4e:    84 f8 05 02       	if (r4 != 1) goto 10 ; target 0x600de5c
 600de52:    40 22             	r0 = 2
 600de54:    41 20             	r1 = 0
 600de56:    bf ea 71 fe       	call -798 ; target 0x600db3c
 600de5a:    55 04             	{pc, r5, r4} = [sp++]
 600de5c:    50 ee 5d 2a       	r2 = b[r5+173] (u)
 600de60:    50 ee 5c 1a       	r1 = b[r5+172] (u)
 600de64:    43 20             	r3 = 0
 600de66:    40 16             	r0 = r4
 600de68:    bf ea 41 ff       	call -382 ; target 0x600dcee
 600de6c:    55 04             	{pc, r5, r4} = [sp++]
 600de6e:    10 04             	[--sp] = rets
 600de70:    01 60             	r1 = [r0+0]
 600de72:    81 f8 11 2c       	if (r1 != 22) goto 34 ; target 0x600de98
 600de76:    08 44             	r0 = b[r0+4] (u)
 600de78:    00 f8 0d 0a       	if (r0 == 5) goto 26 ; target 0x600de96
 600de7c:    80 f8 0c 02       	if (r0 != 1) goto 24 ; target 0x600de98
