cmake_minimum_required(VERSION 3.21)
project(ble_scanner_project LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(simpleble REQUIRED CONFIG)

add_executable(ble_scanner src/main.cpp)
target_link_libraries(ble_scanner PRIVATE simpleble::simpleble)