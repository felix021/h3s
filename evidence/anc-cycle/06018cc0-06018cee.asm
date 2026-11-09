 6018cc0:    74 04             	[--sp] = {rets, r4}
 6018cc2:    04 16             	r4 = r0
 6018cc4:    40 62             	r0 = [r4+8]
 6018cc6:    c1 ff 4f cf 09 06 	r1 = 101306191
 6018ccc:    41 93             	call -218 ; target 0x6018bf4
 6018cce:    bf ea 6b 53       	call -88362 ; target 0x60033a8
 6018cd2:    80 f8 09 02       	if (r0 != 1) goto 18 ; target 0x6018ce8
 6018cd6:    48 4c             	r0 = b[r4+12] (u)
 6018cd8:    00 49             	if (r0 == 0) goto 18 ; target 0x6018cec
 6018cda:    c2 ff 4d 43 4e 41 	r2 = 1095648077
 6018ce0:    80 14             	r1_r0 = 0
 6018ce2:    bf ea ea 86       	call -61996 ; target 0x6009aba
 6018ce6:    00 42             	if (r0 == 0) goto 4 ; target 0x6018cec
 6018ce8:    bf ea 3a ff       	call -396 ; target 0x6018b60
 6018cec:    54 04             	{pc, r4} = [sp++]
