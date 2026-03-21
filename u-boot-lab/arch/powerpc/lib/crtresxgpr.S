/*
 * Relocatable copies of the GCC EABI GPR restore helpers.
 *
 * Old PowerPC U-Boot links with -mrelocatable, so we cannot pull the
 * toolchain's non-relocatable crtresxgpr.o directly from libgcc.a.
 */

	.text

	.globl _restgpr_14_x
	.type _restgpr_14_x,@function
_restgpr_14_x:
	lwz	14,-72(11)

	.globl _restgpr_15_x
	.type _restgpr_15_x,@function
_restgpr_15_x:
	lwz	15,-68(11)

	.globl _restgpr_16_x
	.type _restgpr_16_x,@function
_restgpr_16_x:
	lwz	16,-64(11)

	.globl _restgpr_17_x
	.type _restgpr_17_x,@function
_restgpr_17_x:
	lwz	17,-60(11)

	.globl _restgpr_18_x
	.type _restgpr_18_x,@function
_restgpr_18_x:
	lwz	18,-56(11)

	.globl _restgpr_19_x
	.type _restgpr_19_x,@function
_restgpr_19_x:
	lwz	19,-52(11)

	.globl _restgpr_20_x
	.type _restgpr_20_x,@function
_restgpr_20_x:
	lwz	20,-48(11)

	.globl _restgpr_21_x
	.type _restgpr_21_x,@function
_restgpr_21_x:
	lwz	21,-44(11)

	.globl _restgpr_22_x
	.type _restgpr_22_x,@function
_restgpr_22_x:
	lwz	22,-40(11)

	.globl _restgpr_23_x
	.type _restgpr_23_x,@function
_restgpr_23_x:
	lwz	23,-36(11)

	.globl _restgpr_24_x
	.type _restgpr_24_x,@function
_restgpr_24_x:
	lwz	24,-32(11)

	.globl _restgpr_25_x
	.type _restgpr_25_x,@function
_restgpr_25_x:
	lwz	25,-28(11)

	.globl _restgpr_26_x
	.type _restgpr_26_x,@function
_restgpr_26_x:
	lwz	26,-24(11)

	.globl _restgpr_27_x
	.type _restgpr_27_x,@function
_restgpr_27_x:
	lwz	27,-20(11)

	.globl _restgpr_28_x
	.type _restgpr_28_x,@function
_restgpr_28_x:
	lwz	28,-16(11)

	.globl _restgpr_29_x
	.type _restgpr_29_x,@function
_restgpr_29_x:
	lwz	29,-12(11)

	.globl _restgpr_30_x
	.type _restgpr_30_x,@function
_restgpr_30_x:
	lwz	30,-8(11)

	.globl _restgpr_31_x
	.type _restgpr_31_x,@function
_restgpr_31_x:
	lwz	0,4(11)
	lwz	31,-4(11)
	mtlr	0
	mr	1,11
	blr
