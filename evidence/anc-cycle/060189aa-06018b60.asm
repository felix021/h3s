 60189aa:    76 04             	[--sp] = {rets, r6-r4}
 60189ac:    04 16             	r4 = r0
 60189ae:    71 92             	call -28 ; target 0x6018994
 60189b0:    00 41             	if (r0 == 0) goto 2 ; target 0x60189b4
 60189b2:    56 04             	{pc, r6-r4} = [sp++]
 60189b4:    c5 ff c0 65 12 00 	r5 = 1205696
 60189ba:    c6 ff 80 4c 12 00 	r6 = 1199232
 60189c0:    81 f8 42 02       	if (r1 != 1) goto 132 ; target 0x6018a48
 60189c4:    bf ea d4 7b       	call -67672 ; target 0x6008170
 60189c8:    80 f8 f3 0b       	if (r0 != 5) goto -26 ; target 0x60189b2
 60189cc:    40 25             	r0 = 5
 60189ce:    bf ea 4d 41       	call -97638 ; target 0x6000c6c
 60189d2:    00 43             	if (r0 == 0) goto 6 ; target 0x60189da
 60189d4:    bf ea f2 6d       	call -74780 ; target 0x60065bc
 60189d8:    f0 4c             	if (r0 != 0) goto -40 ; target 0x60189b2
 60189da:    bf ea 83 f6       	call -4858 ; target 0x60176e4
 60189de:    f0 49             	if (r0 != 0) goto -46 ; target 0x60189b2
 60189e0:    61 99             	call -78 ; target 0x6018994
 60189e2:    f0 47             	if (r0 != 0) goto -50 ; target 0x60189b2
 60189e4:    bf ea f8 98       	call -52752 ; target 0x600bbd8
 60189e8:    80 f8 e3 05       	if (r0 != 2) goto -58 ; target 0x60189b2
 60189ec:    50 ee 55 02       	r0 = b[r5+37] (u)
 60189f0:    61 91             	call -94 ; target 0x6018994
 60189f2:    bf ea bd 7b       	call -67718 ; target 0x6008170
 60189f6:    80 f8 9d 0a       	if (r0 != 5) goto 314 ; target 0x6018b34
 60189fa:    40 25             	r0 = 5
 60189fc:    bf ea 36 41       	call -97684 ; target 0x6000c6c
 6018a00:    00 44             	if (r0 == 0) goto 8 ; target 0x6018a0a
 6018a02:    bf ea db 6d       	call -74826 ; target 0x60065bc
 6018a06:    80 f8 95 00       	if (r0 != 0) goto 298 ; target 0x6018b34
 6018a0a:    bf ea 6b f6       	call -4906 ; target 0x60176e4
 6018a0e:    80 f8 91 00       	if (r0 != 0) goto 290 ; target 0x6018b34
 6018a12:    61 80             	call -128 ; target 0x6018994
 6018a14:    80 f8 8e 00       	if (r0 != 0) goto 284 ; target 0x6018b34
 6018a18:    bf ea de 98       	call -52804 ; target 0x600bbd8
 6018a1c:    80 f8 8a 04       	if (r0 != 2) goto 276 ; target 0x6018b34
 6018a20:    50 ee 55 02       	r0 = b[r5+37] (u)
 6018a24:    50 ee 55 02       	r0 = b[r5+37] (u)
 6018a28:    30 52             	if (r0 == 0) goto 228 ; target 0x6018b0e
 6018a2a:    40 20             	r0 = 0
 6018a2c:    d1 ec 55 0d       	[r5+468] = r0
 6018a30:    d1 ec 54 0d       	r0 = [r5+468]
 6018a34:    40 20             	r0 = 0
 6018a36:    bf ea 52 a9       	call -44380 ; target 0x600dcde
 6018a3a:    bf ea 36 9c       	call -51092 ; target 0x600c2aa
 6018a3e:    d0 ec 00 07       	r0 = [r0+112]
 6018a42:    bf ea ee b1       	call -39972 ; target 0x600ee22
 6018a46:    34 8c             	goto 216 ; target 0x6018b20
 6018a48:    84 f8 1c 04       	if (r4 != 2) goto 56 ; target 0x6018a84
 6018a4c:    81 f8 1a 04       	if (r1 != 2) goto 52 ; target 0x6018a84
 6018a50:    40 25             	r0 = 5
 6018a52:    01 e1 a8 50       	r1 = r5 + 168
 6018a56:    98 44             	b[r1+4] = r0
 6018a58:    40 20             	r0 = 0
 6018a5a:    98 45             	b[r1+5] = r0
 6018a5c:    40 21             	r0 = 1
 6018a5e:    98 43             	b[r1+3] = r0
 6018a60:    c0 ff 00 00 a0 c1 	r0 = 3248488448
 6018a66:    d1 ec 55 0d       	[r5+468] = r0
 6018a6a:    d1 ec 54 0d       	r0 = [r5+468]
 6018a6e:    40 21             	r0 = 1
 6018a70:    bf ea c5 9b       	call -51318 ; target 0x600c1fe
 6018a74:    40 21             	r0 = 1
 6018a76:    bf ea 0c 9b       	call -51688 ; target 0x600c092
 6018a7a:    40 22             	r0 = 2
 6018a7c:    41 21             	r1 = 1
 6018a7e:    bf ea 5d a8       	call -44870 ; target 0x600db3c
 6018a82:    24 82             	goto 132 ; target 0x6018b08
 6018a84:    41 20             	r1 = 0
 6018a86:    c0 ff 7d e2 09 06 	r0 = 101311101
 6018a8c:    04 85             	goto 10 ; target 0x6018a98
 6018a8e:    0a 41             	r2 = b[r0+1] (u)
 6018a90:    04 e8 09 20       	if (r2 == r4) goto 18 ; target 0x6018aa6
 6018a94:    c0 22             	r0 += 2
 6018a96:    c1 21             	r1 += 1
 6018a98:    81 fc f9 05       	if (r1 <= 2) goto -14 ; target 0x6018a8e
 6018a9c:    80 ff 9e 41 11 fa 	call -99532386 ; target 0x12cc40
 6018aa2:    40 16             	r0 = r4
 6018aa4:    04 81             	goto 2 ; target 0x6018aa8
 6018aa6:    08 40             	r0 = b[r0+0] (u)
 6018aa8:    52 ee 5b 0a       	b[r5+171] = r0
 6018aac:    04 f8 0f 02       	if (r4 == 1) goto 30 ; target 0x6018ace
 6018ab0:    04 f8 17 06       	if (r4 == 3) goto 46 ; target 0x6018ae2
 6018ab4:    84 f8 21 04       	if (r4 != 2) goto 66 ; target 0x6018afa
 6018ab8:    00 d7             	r0 = r0.b0 (u)  #
 6018aba:    6a 42             		 r2 = b[r6+2] (u)
 6018abc:    69 41             	r1 = b[r6+1] (u)
 6018abe:    43 21             	r3 = 1
 6018ac0:    bf ea 15 a9       	call -44502 ; target 0x600dcee
 6018ac4:    68 41             	r0 = b[r6+1] (u)
 6018ac6:    6c 42             	r4 = b[r6+2] (u)
 6018ac8:    52 ee 5c 0a       	b[r5+172] = r0
 6018acc:    04 94             	goto 40 ; target 0x6018af6
 6018ace:    00 17             	r0 = r0.b0 (u)
 6018ad0:    41 20             	r1 = 0
 6018ad2:    42 20             	r2 = 0
 6018ad4:    43 21             	r3 = 1
 6018ad6:    44 20             	r4 = 0
 6018ad8:    bf ea 09 a9       	call -44526 ; target 0x600dcee
 6018adc:    52 ee 5c 4a       	b[r5+172] = r4
 6018ae0:    04 8a             	goto 20 ; target 0x6018af6
 6018ae2:    00 17             	r0 = r0.b0 (u)
 6018ae4:    41 20             	r1 = 0
 6018ae6:    42 20             	r2 = 0
 6018ae8:    43 21             	r3 = 1
 6018aea:    44 21             	r4 = 1
 6018aec:    bf ea ff a8       	call -44546 ; target 0x600dcee
 6018af0:    52 ee 5c 4a       	b[r5+172] = r4
 6018af4:    44 24             	r4 = 4
 6018af6:    52 ee 5d 4a       	b[r5+173] = r4
 6018afa:    40 e0 63 02       	r0 = 611
 6018afe:    d8 ec 5a 00       	r0 = [r5+r0<<2]
 6018b02:    08 40             	r0 = b[r0+0] (u)
 6018b04:    00 f8 55 0b       	if (r0 == 5) goto -342 ; target 0x60189b2
 6018b08:    bf ea 79 d0       	call -24334 ; target 0x6012bfe
 6018b0c:    56 04             	{pc, r6-r4} = [sp++]
 6018b0e:    bf ea cc 9b       	call -51304 ; target 0x600c2aa
 6018b12:    d0 ec 0c 08       	r0 = [r0+140]
 6018b16:    c1 ff 0c 5e 02 06 	r1 = 100818444
 6018b1c:    bf ea cb a7       	call -45162 ; target 0x600dab6
 6018b20:    50 ee 55 02       	r0 = b[r5+37] (u)
 6018b24:    38 20             	r0 ^= 1
 6018b26:    51 16             	r1 = r5
 6018b28:    5a ee 15 02       	b[++r1=37] = r0
 6018b2c:    40 2f             	r0 = 15
 6018b2e:    42 21             	r2 = 1
 6018b30:    bf ea ef 49       	call -93218 ; target 0x6001f12
 6018b34:    40 21             	r0 = 1
 6018b36:    52 ee 5b 0a       	b[r5+171] = r0
 6018b3a:    bf ea 3e 98       	call -53124 ; target 0x600bbba
 6018b3e:    b0 e8 00 b0       	if (r0 != 0) {
 6018b42:    40 25             		r0 = 5
 6018b44:    52 ee 5c 0a       		b[r5+172] = r0
 6018b48:    40 20             		r0 = 0
 6018b4a:    69 41             		r1 = b[r6+1] (u)
 6018b4c:    68 42             		r0 = b[r6+2] (u)
 6018b4e:    52 ee 5c 1a       		b[r5+172] = r1
 6018b52:    52 ee 5d 0a       	b[r5+173] = r0
 6018b56:    40 21             	r0 = 1
 6018b58:    e8 41             	b[r6+1] = r0
 6018b5a:    40 22             	r0 = 2
 6018b5c:    e8 42             	b[r6+2] = r0
 6018b5e:    e7 94             	goto -88 ; target 0x6018b08
