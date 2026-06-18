// Prevents an extra console window on Windows in release; do NOT remove.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    contextful_lib::run()
}
