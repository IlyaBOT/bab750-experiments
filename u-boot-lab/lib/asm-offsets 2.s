	.file	"asm-offsets.c"
	.machine ppc
	.section	".text"
.Ltext0:
	.file 1 "lib/asm-offsets.c"
	.section	.text.startup.main,"ax",@progbits
	.align 2
	.globl main
	.type	main, @function
main:
.LFB95:
	.loc 1 23 1 view -0
	.cfi_startproc
	stwu 1,-8(1)
	.cfi_def_cfa_offset 8
	mflr 0
	stw 0,12(1)
	.cfi_offset 65, 4
	.loc 1 23 1 is_stmt 0 view .LVU1
	bl __eabi@plt
.LVL0:
	.loc 1 25 2 is_stmt 1 view .LVU2
#APP
 # 25 "lib/asm-offsets.c" 1
	
->GENERATED_GBL_DATA_SIZE 96 (sizeof(struct global_data) + 15) & ~15
 # 0 "" 2
	.loc 1 28 2 view .LVU3
 # 28 "lib/asm-offsets.c" 1
	
->GENERATED_BD_INFO_SIZE 64 (sizeof(struct bd_info) + 15) & ~15
 # 0 "" 2
	.loc 1 31 2 view .LVU4
	.loc 1 32 1 is_stmt 0 view .LVU5
#NO_APP
	lwz 0,12(1)
	li 3,0
	addi 1,1,8
	.cfi_def_cfa_offset 0
	mtlr 0
	.cfi_restore 65
	blr
	.cfi_endproc
.LFE95:
	.size	main, .-main
	.section	".text"
.Letext0:
	.section	.debug_info,"",@progbits
.Ldebug_info0:
	.4byte	0xa4
	.2byte	0x5
	.byte	0x1
	.byte	0x4
	.4byte	.Ldebug_abbrev0
	.uleb128 0x2
	.4byte	.LASF11
	.byte	0x1
	.4byte	.LASF12
	.4byte	.LASF13
	.4byte	.LLRL0
	.4byte	0
	.4byte	.Ldebug_line0
	.uleb128 0x1
	.byte	0x1
	.byte	0x8
	.4byte	.LASF0
	.uleb128 0x1
	.byte	0x4
	.byte	0x7
	.4byte	.LASF1
	.uleb128 0x1
	.byte	0x2
	.byte	0x7
	.4byte	.LASF2
	.uleb128 0x1
	.byte	0x4
	.byte	0x7
	.4byte	.LASF3
	.uleb128 0x1
	.byte	0x1
	.byte	0x6
	.4byte	.LASF4
	.uleb128 0x1
	.byte	0x2
	.byte	0x5
	.4byte	.LASF5
	.uleb128 0x3
	.byte	0x4
	.byte	0x5
	.string	"int"
	.uleb128 0x1
	.byte	0x8
	.byte	0x5
	.4byte	.LASF6
	.uleb128 0x1
	.byte	0x8
	.byte	0x7
	.4byte	.LASF7
	.uleb128 0x1
	.byte	0x4
	.byte	0x5
	.4byte	.LASF8
	.uleb128 0x1
	.byte	0x1
	.byte	0x8
	.4byte	.LASF9
	.uleb128 0x1
	.byte	0x8
	.byte	0x4
	.4byte	.LASF10
	.uleb128 0x4
	.4byte	.LASF14
	.byte	0x1
	.byte	0x16
	.byte	0x5
	.4byte	0x50
	.4byte	.LFB95
	.4byte	.LFE95-.LFB95
	.uleb128 0x1
	.byte	0x9c
	.4byte	0x9e
	.uleb128 0x5
	.4byte	.LVL0
	.4byte	0x9e
	.byte	0
	.uleb128 0x6
	.4byte	.LASF15
	.4byte	.LASF15
	.byte	0
	.section	.debug_abbrev,"",@progbits
.Ldebug_abbrev0:
	.uleb128 0x1
	.uleb128 0x24
	.byte	0
	.uleb128 0xb
	.uleb128 0xb
	.uleb128 0x3e
	.uleb128 0xb
	.uleb128 0x3
	.uleb128 0xe
	.byte	0
	.byte	0
	.uleb128 0x2
	.uleb128 0x11
	.byte	0x1
	.uleb128 0x25
	.uleb128 0xe
	.uleb128 0x13
	.uleb128 0xb
	.uleb128 0x3
	.uleb128 0xe
	.uleb128 0x1b
	.uleb128 0xe
	.uleb128 0x55
	.uleb128 0x17
	.uleb128 0x11
	.uleb128 0x1
	.uleb128 0x10
	.uleb128 0x17
	.byte	0
	.byte	0
	.uleb128 0x3
	.uleb128 0x24
	.byte	0
	.uleb128 0xb
	.uleb128 0xb
	.uleb128 0x3e
	.uleb128 0xb
	.uleb128 0x3
	.uleb128 0x8
	.byte	0
	.byte	0
	.uleb128 0x4
	.uleb128 0x2e
	.byte	0x1
	.uleb128 0x3f
	.uleb128 0x19
	.uleb128 0x3
	.uleb128 0xe
	.uleb128 0x3a
	.uleb128 0xb
	.uleb128 0x3b
	.uleb128 0xb
	.uleb128 0x39
	.uleb128 0xb
	.uleb128 0x27
	.uleb128 0x19
	.uleb128 0x49
	.uleb128 0x13
	.uleb128 0x11
	.uleb128 0x1
	.uleb128 0x12
	.uleb128 0x6
	.uleb128 0x40
	.uleb128 0x18
	.uleb128 0x7a
	.uleb128 0x19
	.uleb128 0x1
	.uleb128 0x13
	.byte	0
	.byte	0
	.uleb128 0x5
	.uleb128 0x48
	.byte	0
	.uleb128 0x7d
	.uleb128 0x1
	.uleb128 0x7f
	.uleb128 0x13
	.byte	0
	.byte	0
	.uleb128 0x6
	.uleb128 0x2e
	.byte	0
	.uleb128 0x3f
	.uleb128 0x19
	.uleb128 0x3c
	.uleb128 0x19
	.uleb128 0x6e
	.uleb128 0xe
	.uleb128 0x3
	.uleb128 0xe
	.byte	0
	.byte	0
	.byte	0
	.section	.debug_aranges,"",@progbits
	.4byte	0x1c
	.2byte	0x2
	.4byte	.Ldebug_info0
	.byte	0x4
	.byte	0
	.2byte	0
	.2byte	0
	.4byte	.LFB95
	.4byte	.LFE95-.LFB95
	.4byte	0
	.4byte	0
	.section	.debug_rnglists,"",@progbits
.Ldebug_ranges0:
	.4byte	.Ldebug_ranges3-.Ldebug_ranges2
.Ldebug_ranges2:
	.2byte	0x5
	.byte	0x4
	.byte	0
	.4byte	0
.LLRL0:
	.byte	0x7
	.4byte	.LFB95
	.uleb128 .LFE95-.LFB95
	.byte	0
.Ldebug_ranges3:
	.section	.debug_line,"",@progbits
.Ldebug_line0:
	.section	.debug_str,"MS",@progbits,1
.LASF6:
	.string	"long long int"
.LASF3:
	.string	"unsigned int"
.LASF15:
	.string	"__eabi"
.LASF14:
	.string	"main"
.LASF1:
	.string	"long unsigned int"
.LASF7:
	.string	"long long unsigned int"
.LASF0:
	.string	"unsigned char"
.LASF12:
	.string	"lib/asm-offsets.c"
.LASF9:
	.string	"char"
.LASF8:
	.string	"long int"
.LASF10:
	.string	"double"
.LASF2:
	.string	"short unsigned int"
.LASF13:
	.string	"/Users/ilyabot/Documents/Projects/bab750-experiments/u-boot-lab"
.LASF11:
	.string	"GNU C89 15.2.0 -mcall-sysv -mrelocatable -meabi -mcpu=750 -g -Os -std=gnu90 -fpic -ffunction-sections -fdata-sections -fno-builtin -ffreestanding -ffixed-r2 -fgnu89-inline -fno-stack-protector -fstack-usage"
.LASF5:
	.string	"short int"
.LASF4:
	.string	"signed char"
	.globl __eabi
	.ident	"GCC: (devkitPPC) 15.2.0"
