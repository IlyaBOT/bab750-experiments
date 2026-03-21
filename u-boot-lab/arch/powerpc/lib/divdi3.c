/*
 * Relocatable copy of the generic libgcc 64-bit signed division helper.
 *
 * This keeps old PowerPC U-Boot builds working with toolchains whose
 * libgcc.a is built without -mrelocatable.
 */

typedef unsigned int UWtype;
typedef unsigned int UHWtype;
typedef unsigned long long UDWtype;
typedef unsigned char UQItype;
typedef int word_type;
typedef long Wtype;
typedef long long DWtype;

#define W_TYPE_SIZE	32
#define __BITS4		(W_TYPE_SIZE / 4)
#define __ll_B		((UWtype)1 << (W_TYPE_SIZE / 2))
#define __ll_lowpart(t)	((UWtype)(t) & (__ll_B - 1))
#define __ll_highpart(t) ((UWtype)(t) >> (W_TYPE_SIZE / 2))

struct DWstruct {
	Wtype low;
	Wtype high;
};

typedef union {
	struct DWstruct s;
	DWtype ll;
} DWunion;

static const UQItype __clz_tab[256] = {
	0, 1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4,
	5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
	6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
	6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
	8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8
};

#define sub_ddmmss(sh, sl, ah, al, bh, bl) \
	do { \
		UWtype __x; \
		__x = (al) - (bl); \
		(sh) = (ah) - (bh) - (__x > (al)); \
		(sl) = __x; \
	} while (0)

#define umul_ppmm(w1, w0, u, v) \
	do { \
		UWtype __x0, __x1, __x2, __x3; \
		UHWtype __ul, __vl, __uh, __vh; \
		__ul = __ll_lowpart(u); \
		__uh = __ll_highpart(u); \
		__vl = __ll_lowpart(v); \
		__vh = __ll_highpart(v); \
		__x0 = (UWtype)__ul * __vl; \
		__x1 = (UWtype)__ul * __vh; \
		__x2 = (UWtype)__uh * __vl; \
		__x3 = (UWtype)__uh * __vh; \
		__x1 += __ll_highpart(__x0); \
		__x1 += __x2; \
		if (__x1 < __x2) \
			__x3 += __ll_B; \
		(w1) = __x3 + __ll_highpart(__x1); \
		(w0) = __ll_lowpart(__x1) * __ll_B + __ll_lowpart(__x0); \
	} while (0)

#define __udiv_qrnnd_c(q, r, n1, n0, d) \
	do { \
		UWtype __d1, __d0, __q1, __q0; \
		UWtype __r1, __r0, __m; \
		__d1 = __ll_highpart(d); \
		__d0 = __ll_lowpart(d); \
		__r1 = (n1) % __d1; \
		__q1 = (n1) / __d1; \
		__m = (UWtype)__q1 * __d0; \
		__r1 = __r1 * __ll_B | __ll_highpart(n0); \
		if (__r1 < __m) { \
			__q1--; \
			__r1 += (d); \
			if (__r1 >= (d)) \
				if (__r1 < __m) { \
					__q1--; \
					__r1 += (d); \
				} \
		} \
		__r1 -= __m; \
		__r0 = __r1 % __d1; \
		__q0 = __r1 / __d1; \
		__m = (UWtype)__q0 * __d0; \
		__r0 = __r0 * __ll_B | __ll_lowpart(n0); \
		if (__r0 < __m) { \
			__q0--; \
			__r0 += (d); \
			if (__r0 >= (d)) \
				if (__r0 < __m) { \
					__q0--; \
					__r0 += (d); \
				} \
		} \
		__r0 -= __m; \
		(q) = (UWtype)__q1 * __ll_B | __q0; \
		(r) = __r0; \
	} while (0)

#define UDIV_NEEDS_NORMALIZATION 1
#define udiv_qrnnd __udiv_qrnnd_c

#define count_leading_zeros(count, x) \
	do { \
		UWtype __xr = (x); \
		UWtype __a; \
		__a = __xr < ((UWtype)1 << 2 * __BITS4) ? \
			(__xr < ((UWtype)1 << __BITS4) ? 0 : __BITS4) : \
			(__xr < ((UWtype)1 << 3 * __BITS4) ? 2 * __BITS4 : 3 * __BITS4); \
		(count) = W_TYPE_SIZE - (__clz_tab[__xr >> __a] + __a); \
	} while (0)

static DWunion neg_dwunion(DWunion u)
{
	DWunion w;

	w.s.low = -u.s.low;
	w.s.high = -u.s.high - ((UWtype)w.s.low > 0);

	return w;
}

static UDWtype udivmoddi4(UDWtype n, UDWtype d, UDWtype *rp)
{
	DWunion nn;
	DWunion dd;
	DWunion rr;
	DWunion ww;
	UWtype d0, d1, n0, n1, n2;
	UWtype q0, q1;
	UWtype b, bm;
	UWtype m1, m0;

	nn.ll = n;
	dd.ll = d;
	d0 = dd.s.low;
	d1 = dd.s.high;
	n0 = nn.s.low;
	n1 = nn.s.high;

	if (d1 == 0) {
		if (d0 > n1) {
			count_leading_zeros(bm, d0);

			if (bm != 0) {
				d0 = d0 << bm;
				n1 = (n1 << bm) | (n0 >> (W_TYPE_SIZE - bm));
				n0 = n0 << bm;
			}

			udiv_qrnnd(q0, n0, n1, n0, d0);
			q1 = 0;
		} else {
			if (d0 == 0)
				d0 = 1 / d0;

			count_leading_zeros(bm, d0);

			if (bm == 0) {
				n1 -= d0;
				q1 = 1;
			} else {
				b = W_TYPE_SIZE - bm;
				d0 = d0 << bm;
				n2 = n1 >> b;
				n1 = (n1 << bm) | (n0 >> b);
				n0 = n0 << bm;

				udiv_qrnnd(q1, n1, n2, n1, d0);
			}

			udiv_qrnnd(q0, n0, n1, n0, d0);
		}

		if (rp != 0) {
			rr.s.low = n0 >> bm;
			rr.s.high = 0;
			*rp = rr.ll;
		}
	} else {
		if (d1 > n1) {
			q0 = 0;
			q1 = 0;

			if (rp != 0) {
				rr.s.low = n0;
				rr.s.high = n1;
				*rp = rr.ll;
			}
		} else {
			count_leading_zeros(bm, d1);

			if (bm == 0) {
				if (n1 > d1 || n0 >= d0) {
					q0 = 1;
					sub_ddmmss(n1, n0, n1, n0, d1, d0);
				} else {
					q0 = 0;
				}

				q1 = 0;

				if (rp != 0) {
					rr.s.low = n0;
					rr.s.high = n1;
					*rp = rr.ll;
				}
			} else {
				b = W_TYPE_SIZE - bm;
				d1 = (d1 << bm) | (d0 >> b);
				d0 = d0 << bm;
				n2 = n1 >> b;
				n1 = (n1 << bm) | (n0 >> b);
				n0 = n0 << bm;

				udiv_qrnnd(q0, n1, n2, n1, d1);
				umul_ppmm(m1, m0, q0, d0);

				if (m1 > n1 || (m1 == n1 && m0 > n0)) {
					q0--;
					sub_ddmmss(m1, m0, m1, m0, d1, d0);
				}

				q1 = 0;

				if (rp != 0) {
					sub_ddmmss(n1, n0, n1, n0, m1, m0);
					rr.s.low = (n1 << b) | (n0 >> bm);
					rr.s.high = n1 >> bm;
					*rp = rr.ll;
				}
			}
		}
	}

	ww.s.low = q0;
	ww.s.high = q1;

	return ww.ll;
}

DWtype __divdi3(DWtype u, DWtype v)
{
	word_type c;
	DWunion uu;
	DWunion vv;
	DWunion ww;

	c = 0;
	uu.ll = u;
	vv.ll = v;

	if (uu.s.high < 0) {
		c = ~c;
		uu = neg_dwunion(uu);
	}

	if (vv.s.high < 0) {
		c = ~c;
		vv = neg_dwunion(vv);
	}

	ww.ll = udivmoddi4(uu.ll, vv.ll, (UDWtype *)0);

	if (c)
		ww = neg_dwunion(ww);

	return ww.ll;
}
