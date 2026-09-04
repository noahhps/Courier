//! The desktop shell.
//!
//! Deliberately thin. Everything the app *does* still lives in the React
//! client and the Python server -- this crate owns only the things a webview
//! cannot do for itself: a menu bar presence, a window that hides instead of
//! quitting, and supervising the server process.
//!
//! Keeping it thin is what makes the Windows client cheap later: none of the
//! product logic is in here to be ported.

mod quickview;
mod server;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, RunEvent, WindowEvent,
};

use server::ManagedServer;

/// Bring the window back to the front, un-hiding it first if the close button
/// put it away. Used by both the tray menu and a left-click on the icon.
fn present_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(ManagedServer::default())
        .setup(|app| {
            let handle = app.handle();
            quickview::setup(handle);
            let bound = quickview::register(handle);

            let open = MenuItem::with_id(app, "open", "Open Courier", true, None::<&str>)?;
            // Named with the key it answers to, so the tray is where you find
            // out what the shortcut is. When registration failed there is no
            // key to name, and the item says so rather than lying.
            let quick = MenuItem::with_id(
                app,
                "quickview",
                &match &bound {
                    Some(spec) => format!("QuickView  ({spec})"),
                    None => "QuickView  (no shortcut)".to_string(),
                },
                true,
                None::<&str>,
            )?;
            let quit = MenuItem::with_id(app, "quit", "Quit Courier", true, Some("Cmd+Q"))?;
            let menu = Menu::with_items(app, &[&open, &quick, &quit])?;

            TrayIconBuilder::with_id("courier-tray")
                // The app icon, for now. Next event and reachability replace
                // this once there is a server to ask.
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                // macOS convention: the icon is a menu, not a button. Left
                // click opening the window instead would make the menu
                // reachable only by right-click, which nobody discovers.
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => present_main_window(app),
                    "quickview" => quickview::toggle(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            // The window is configured hidden and shown here, once the API is
            // answering. Booting it earlier means the client's first request
            // races a server that is still importing numpy, and loses -- which
            // reaches the reader as "that token was rejected" rather than as
            // "wait a moment".
            //
            // On its own thread because it blocks: `setup` runs before the
            // event loop, so waiting here would freeze the tray too.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let ready = server::ensure_running(&handle);
                if !ready {
                    // Shown anyway. The gate can explain an unreachable server
                    // and let the reader point somewhere else; a window that
                    // never appears cannot.
                    eprintln!("[server] opening the window without a local server");
                }
                present_main_window(&handle);
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // The close button hides rather than quits. An assistant that is
            // supposed to be always available should not need relaunching
            // because the window was in the way -- and quitting now also stops
            // the server, which is a much bigger deal than closing a window
            // looks.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Courier");

    app.run(|handle, event| {
        // Exit is the one place the server can be stopped from: window close
        // is a hide, and the tray's Quit routes here too.
        if let RunEvent::Exit = event {
            server::shutdown(handle);
        }
    });
}
