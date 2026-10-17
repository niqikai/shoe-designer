/* DYLD interpose shim: make sysconf(_SC_NPROCESSORS_*) and sysctl hw.*cpu report N CPUs
 * (env ONECPU_N, default 1), so a statically linked TBB in a wheel starts N threads.
 * Build: clang -dynamiclib -o .scratch/interpose/libonecpu.dylib spikes/m0_6a/onecpu.c
 * Use:   DYLD_INSERT_LIBRARIES=.scratch/interpose/libonecpu.dylib ONECPU_N=1 python ...   */
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/sysctl.h>

static int ncpu(void) { const char *s = getenv("ONECPU_N"); int n = s ? atoi(s) : 1; return n > 0 ? n : 1; }

static long my_sysconf(int name) {
  if (name == _SC_NPROCESSORS_ONLN || name == _SC_NPROCESSORS_CONF) return ncpu();
  return sysconf(name);
}
static int my_sysctlbyname(const char *n, void *oldp, size_t *oldlenp, void *newp, size_t newlen) {
  if (n && oldp && oldlenp && *oldlenp >= sizeof(int) &&
      (!strcmp(n, "hw.ncpu") || !strcmp(n, "hw.logicalcpu") || !strcmp(n, "hw.logicalcpu_max") ||
       !strcmp(n, "hw.activecpu") || !strcmp(n, "hw.availcpu") || !strcmp(n, "hw.physicalcpu") ||
       !strcmp(n, "hw.physicalcpu_max"))) {
    if (*oldlenp == sizeof(int)) *(int *)oldp = ncpu(); else *(long long *)oldp = ncpu();
    return 0;
  }
  return sysctlbyname(n, oldp, oldlenp, newp, newlen);
}
static int my_sysctl(int *name, u_int namelen, void *oldp, size_t *oldlenp, void *newp, size_t newlen) {
  if (namelen == 2 && name[0] == CTL_HW && (name[1] == HW_NCPU || name[1] == HW_AVAILCPU) && oldp && oldlenp && *oldlenp >= sizeof(int)) {
    *(int *)oldp = ncpu(); *oldlenp = sizeof(int); return 0;
  }
  return sysctl(name, namelen, oldp, oldlenp, newp, newlen);
}
#define INTERPOSE(r, o) __attribute__((used)) static struct { const void *r_; const void *o_; } _ip_##o __attribute__((section("__DATA,__interpose"))) = { (const void *)(unsigned long)&r, (const void *)(unsigned long)&o }
INTERPOSE(my_sysconf, sysconf);
INTERPOSE(my_sysctlbyname, sysctlbyname);
INTERPOSE(my_sysctl, sysctl);
