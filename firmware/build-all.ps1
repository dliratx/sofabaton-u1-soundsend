# Offline compilation and validation only. This script has no BLE/update step.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $projectRoot 'research\zig-toolchain\zig-x86_64-windows-0.15.2\zig.exe'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$previousLocalCache = $env:ZIG_LOCAL_CACHE_DIR
$previousGlobalCache = $env:ZIG_GLOBAL_CACHE_DIR
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $projectRoot '.cache\zig-local'
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot '.cache\zig-global'
$common = @('cc', '-target', 'thumb-freestanding-eabi', '-mcpu=cortex_m0', '-std=c99',
    '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-Oz', '-g',
    '-Wall', '-Wextra', '-Werror', '-nostdlib', '-Wl,--build-id=none')
Push-Location $projectRoot
try {
    & $python firmware/build_ble_tables.py
    if ($LASTEXITCODE -ne 0) { throw 'BLE packet table generation failed' }
    & $compiler @common '-Wl,-T,firmware/emulation-only.ld' '-Wl,--entry=ss_init' `
        firmware/soundsend_client.c -o firmware/soundsend-client-emulation.elf
    if ($LASTEXITCODE -ne 0) { throw 'ATT compilation failed' }
    & $compiler @common '-Wl,-T,firmware/emulation-only.ld' '-Wl,--entry=bc_init' `
        firmware/soundsend_client.c firmware/ble_central.c -o firmware/central-emulation.elf
    if ($LASTEXITCODE -ne 0) { throw 'Central compilation failed' }
    & $compiler @common '-Wl,-T,firmware/u1-lab.ld' '-Wl,--entry=u1_boot_gateway' `
        firmware/soundsend_client.c firmware/ble_central.c firmware/ble_packet.c firmware/a8107_port.c `
        firmware/u1_integration.c firmware/u1_trampolines.S -o firmware/u1-lab-module.elf
    if ($LASTEXITCODE -ne 0) { throw 'Integrated lab compilation failed' }
    & $python firmware/build_u1_lab.py
    if ($LASTEXITCODE -ne 0) { throw 'Image assembly failed' }
    & $python firmware/build_ota_lab.py
    if ($LASTEXITCODE -ne 0) { throw 'OTA file assembly failed' }
    foreach ($test in @('test_client_arm.py', 'test_central_arm.py', 'test_ble_packet_arm.py', 'test_u1_lab_arm.py', 'test_lcd_retention_arm.py', 'test_ota_lab_arm.py', 'test_ota_transport.py')) {
        & $python (Join-Path 'firmware' $test)
        if ($LASTEXITCODE -ne 0) { throw "Failed: $test" }
    }
} finally {
    Pop-Location
    $env:ZIG_LOCAL_CACHE_DIR = $previousLocalCache
    $env:ZIG_GLOBAL_CACHE_DIR = $previousGlobalCache
}
