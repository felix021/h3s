 6018b60:    76 04             	[--sp] = {rets, r6-r4}
 6018b62:    c4 ff c0 65 12 00 	r4 = 1205696
 6018b68:    d1 ec 44 0f       	r0 = [r4+500]
 6018b6c:    10 5b             	if (r0 == 0) goto 118 ; target 0x6018be4
 6018b6e:    bf ea b9 f5       	call -5262 ; target 0x60176e4
 6018b72:    90 58             	if (r0 != 0) goto 112 ; target 0x6018be4
 6018b74:    80 ff e2 29 11 fa 	call -99538462 ; target 0x12b55c
 6018b7a:    d1 ec 44 0f       	r0 = [r4+500]
 6018b7e:    41 21             	r1 = 1
 6018b80:    52 ee 04 12       	b[r0+36] = r1
 6018b84:    45 20             	r5 = 0
 6018b86:    c4 ff 10 e5 09 06 	r4 = 101311760
 6018b8c:    41 20             	r1 = 0
 6018b8e:    42 20             	r2 = 0
 6018b90:    02 fc 19 04       	if (r2 > 2) goto 50 ; target 0x6018bc6
 6018b94:    d8 ee 20 34       	r3 = b[r2+r4] (u)
 6018b98:    50 ee 01 62       	r6 = b[r0+33] (u)
 6018b9c:    c1 21             	r1 += 1
 6018b9e:    c2 21             	r2 += 1
 6018ba0:    86 e8 f6 31       	if (r3 != r6) goto -20 ; target 0x6018b90
 6018ba4:    12 17             	r2 = r1.b0 (u)
 6018ba6:    b2 ec 02 00       	if (r2 <= 2) {
 6018baa:    15 16             		r5 = r1
 6018bac:    51 d7             	r1 = r5.b0 (u)  #
 6018bae:    08 41             		 r0 = b[r0+1] (u)
 6018bb0:    d8 ee 10 14       	r1 = b[r1+r4] (u)
 6018bb4:    94 e1 02 01       	r0 = r0 & (1 << r1)
 6018bb8:    80 46             	if (r0 != 0) goto 12 ; target 0x6018bc6
 6018bba:    58 81             	r0 = r5 + 1
 6018bbc:    01 17             	r1 = r0.b0 (u)
 6018bbe:    45 20             	r5 = 0
 6018bc0:    b1 ec 02 00       	if (r1 <= 2) {
 6018bc4:    05 16             		r5 = r0
 6018bc6:    80 ff e4 29 11 fa 	call -99538460 ; target 0x12b5b0
 6018bcc:    55 17             	r5 = r5.b0 (u)
 6018bce:    bf ea f4 97       	call -53272 ; target 0x600bbba
 6018bd2:    d8 ee 50 24       	r2 = b[r5+r4] (u)
 6018bd6:    41 21             	r1 = 1
 6018bd8:    80 41             	if (r0 != 0) goto 2 ; target 0x6018bdc
 6018bda:    01 16             	r1 = r0
 6018bdc:    11 a1             	r1 = r1 << 1
 6018bde:    20 16             	r0 = r2
 6018be0:    bf ea e3 fe       	call -570 ; target 0x60189aa
 6018be4:    56 04             	{pc, r6-r4} = [sp++]
