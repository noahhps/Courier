// Prevents an extra console window on Windows in release. Harmless on macOS,
// and here from the start so the Windows client does not have to remember it.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    courier_lib::run()
}
