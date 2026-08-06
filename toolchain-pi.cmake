# CMake toolchain file for cross-compiling to the Pi Zero 2 W from WSL2.
#
#     sudo apt install crossbuild-essential-arm64
#     cmake -B build-pi -DCMAKE_TOOLCHAIN_FILE=toolchain-pi.cmake \
#           -DPI_SYSROOT=$HOME/pi-sysroot
#     cmake --build build-pi
#
# Targets aarch64 (64-bit). The board is an ARMv8 Cortex-A53 and this guide
# recommends arm64 for both devices: 32-bit ARM is being progressively
# deprecated across the Linux ecosystem, and every library here — libgpiod,
# libmicrohttpd — is fully supported on arm64. The previous 32-bit target used
# crossbuild-essential-armhf / arm-linux-gnueabihf.

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER   aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

# A sysroot holding the target's headers and libraries — libmicrohttpd-dev and
# libgpiod-dev as installed on the Pi. Populate it by rsyncing from a running
# board:
#
#     rsync -avz --rsync-path="sudo rsync" \
#         pi@console.local:/usr/include pi@console.local:/usr/lib \
#         $HOME/pi-sysroot/
#
# Without this, pkg-config finds the build machine's x86-64 libraries and the
# link fails with architecture mismatches that look like missing symbols.
if(DEFINED PI_SYSROOT)
    set(CMAKE_SYSROOT ${PI_SYSROOT})
    set(CMAKE_FIND_ROOT_PATH ${PI_SYSROOT})

    # Point pkg-config at the sysroot too, or it will happily report the host's
    # flags for a cross build.
    set(ENV{PKG_CONFIG_DIR} "")
    set(ENV{PKG_CONFIG_LIBDIR}
        "${PI_SYSROOT}/usr/lib/aarch64-linux-gnu/pkgconfig:${PI_SYSROOT}/usr/share/pkgconfig")
    set(ENV{PKG_CONFIG_SYSROOT_DIR} "${PI_SYSROOT}")
endif()

# Look for programs on the host, but headers and libraries only in the target
# sysroot.
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM BEFORE)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# Cortex-A53. Tuning for it is free — the binary only ever runs on this part.
set(CMAKE_C_FLAGS_INIT "-mcpu=cortex-a53")
