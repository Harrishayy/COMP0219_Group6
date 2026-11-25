/* newlib's float formatter allocates a small amount of memory. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>

void *_sbrk(ptrdiff_t increment) {
  extern char _end, _estack;
  static char *heap;
  if (!heap) heap = &_end;
  uintptr_t current = (uintptr_t)heap;
  uintptr_t limit = (uintptr_t)&_estack - 4096U;
  if (increment < 0 || current > limit || (uintptr_t)increment > limit - current) {
    errno = ENOMEM;
    return (void *)-1;
  }
  char *previous = heap;
  heap += increment;
  return previous;
}
