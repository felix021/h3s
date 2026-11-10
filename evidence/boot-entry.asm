# Actual official QCY 0.1.1.8 application instruction excerpts.
# app.bin SHA256: 18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041
# Vendor pi32v2/r3 disassembly; call displacements are relative to instruction END.
# Excerpts only; not a full application image or recovered vendor source.

 6000100:    ee ff 40 38 12 00 	sp = 1194048
 6000106:    ed ff 40 38 12 00 	ssp = 1194048
 600010c:    d8 e8 07 00       	[--sp] = {r2-r0}
 6000110:    80 ff 00 55 03 00 	call 218368
 6000116:    d4 e8 07 00       	{r2-r0} = [sp++]
 600011a:    80 ff b8 63 06 00 	call 418744
 6035616:    10 04             	[--sp] = rets
 6035618:    c0 ff 00 46 fd 00 	r0 = 16598528
 603561e:    00 60             	r0 = [r0+0]
 6035620:    50 e8 0e 02       	if ((r0 & 1) != 0) goto 28
 6035624:    c0 ff 28 fc 19 00 	r0 = 1702952
 603562a:    c2 ff 00 36 6e 01 	r2 = 24000000
 6035630:    c3 ff 00 6c dc 02 	r3 = 48000000
 6035636:    50 ec 05 20       	d[r0+4] = r3_r2
 603563a:    80 ff d4 a7 fe f9 	call -100751404
 6035640:    00 04             	pc = [sp++]
 60664d8:    50 ec 04 20       	r3_r2 = d[r0+4]
 60664dc:    c1 ff 00 28 12 00 	r1 = 1189888
 60664e2:    50 ec 11 21       	d[r1+16] = r3_r2
 60664e6:    02 60             	r2 = [r0+0]
 60664e8:    23 62             	r3 = [r2+8]
 60664ea:    93 66             	[r1+24] = r3
 60664ec:    2a 4d             	r2 = b[r2+13] (u)
 60664ee:    9a 40             	b[r1+0] = r2
 60664f0:    0a 66             	r2 = h[r0+12] (u)
 60664f2:    9a 6e             	h[r1+28] = r2
 60664f4:    0a 67             	r2 = h[r0+14] (u)
 60664f6:    9a 6f             	h[r1+30] = r2
 60664f8:    50 ec 00 21       	r3_r2 = d[r0+16]
 60664fc:    50 ec 11 22       	d[r1+32] = r3_r2
 6066500:    80 00             	rts
