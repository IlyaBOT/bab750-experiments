#ifndef __LINUX_COMPILER_H
#error "Please don't include <linux/compiler-gcc15.h> directly, include <linux/compiler.h> instead."
#endif

/*
 * Compatibility shim for very old U-Boot / Linux-derived trees that select
 * compiler-specific headers strictly by GCC major version.
 *
 * GCC 15 is new enough that these trees do not provide compiler-gcc15.h at all.
 * Reuse the newest legacy header available in the tree.
 */

#if defined(__has_include)
# if __has_include(<linux/compiler-gcc5.h>)
#  include <linux/compiler-gcc5.h>
# elif __has_include(<linux/compiler-gcc4.h>)
#  include <linux/compiler-gcc4.h>
# elif __has_include(<linux/compiler-gcc3.h>)
#  include <linux/compiler-gcc3.h>
# else
#  error "No legacy linux/compiler-gcc[3-5].h header found in this source tree."
# endif
#else
# include <linux/compiler-gcc4.h>
#endif
