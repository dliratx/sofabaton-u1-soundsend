$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $projectRoot 'research\zig-toolchain\zig-x86_64-windows-0.15.2\zig.exe'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$previousLocalCache = $env:ZIG_LOCAL_CACHE_DIR
$previousGlobalCache = $env:ZIG_GLOBAL_CACHE_DIR
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $projectRoot '.cache\zig-local'
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot '.cache\zig-global'
Push-Location $projectRoot
try {
    & $compiler cc -target thumb-freestanding-eabi -mcpu=cortex_m0 -std=c99 `
        -ffreestanding -fno-builtin -fno-stack-protector -Oz -g `
        -Wall -Wextra -Werror -nostdlib '-Wl,-T,firmware/emulation-only.ld' `
        '-Wl,--entry=ss_init' '-Wl,--build-id=none' firmware/soundsend_client.c `
        -o firmware/soundsend-client-emulation.elf
    if ($LASTEXITCODE -ne 0) { throw 'ARM compilation failed' }
    & $python firmware/test_client_arm.py
    if ($LASTEXITCODE -ne 0) { throw 'ARM behavior tests failed' }
} finally {
    Pop-Location
    $env:ZIG_LOCAL_CACHE_DIR = $previousLocalCache
    $env:ZIG_GLOBAL_CACHE_DIR = $previousGlobalCache
}
