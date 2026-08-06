cmake_minimum_required(VERSION 3.16)
project(operatorconsole C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Warnings on, and warnings are errors. On a device that runs unattended for
# months, a warning is a defect that has not surfaced yet.
add_compile_options(-Wall -Wextra -Wpedantic -Werror)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

find_package(PkgConfig REQUIRED)

# libmicrohttpd: the embedded HTTP server (GNU project).
pkg_check_modules(MHD REQUIRED libmicrohttpd)

# libgpiod v2. The v1 API is neither available on Raspberry Pi OS Trixie nor
# compatible, so the version floor is enforced here rather than discovered as a
# wall of compile errors. Note libgpiod3 (runtime) is installed by default but
# libgpiod-dev (headers, linker symlink, pkg-config metadata) is not:
#     sudo apt install libgpiod-dev
pkg_check_modules(GPIOD REQUIRED libgpiod>=2.0)

add_executable(operatorconsole
    src/main.c
    src/webserver.c
    src/hardware/gpio.c
    src/hardware/i2c.c
    src/hardware/spi.c
)

target_include_directories(operatorconsole PRIVATE
    src
    ${MHD_INCLUDE_DIRS}
    ${GPIOD_INCLUDE_DIRS}
)

target_link_directories(operatorconsole PRIVATE
    ${MHD_LIBRARY_DIRS}
    ${GPIOD_LIBRARY_DIRS}
)

target_link_libraries(operatorconsole PRIVATE
    ${MHD_LIBRARIES}
    ${GPIOD_LIBRARIES}
    pthread
)

install(TARGETS operatorconsole RUNTIME DESTINATION bin)

# The front end is data, not code: it ships alongside the binary and is served
# from disk, so it is installed rather than embedded.
install(DIRECTORY www/ DESTINATION share/operatorconsole/www)
install(PROGRAMS kiosk.sh DESTINATION bin)
