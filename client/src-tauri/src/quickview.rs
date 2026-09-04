//! The hotkey panel.
//!
//! A temporary conversation summoned over whatever the reader is already doing,
//! and dismissed without ceremony. Everything it does once it is open belongs to
//! the React app and the server; what lives here is the part a webview cannot do
//! for itself -- a shortcut that fires while another application is focused, a
//! window with no chrome that floats above everything, and the frosted material
//! behind it.
//!
//! The shortcut is configurable and defaults to Ctrl+Space, which on macOS is
//! *already taken*: it is the system default for "Select the previous input
//! source". Registration then fails, and this says so out loud rather than
//! leaving a hotkey that silently does nothing -- see `register`.

use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// What the panel opens on when nothing has been configured.
pub const DEFAULT_SHORTCUT: &str = "ctrl+space";

/// The window label, matching `tauri.conf.json`.
const LABEL: &str = "quickview";

/// Read the shortcut from the environment, falling back to the default.
///
/// An environment variable rather than a settings file *for now*: the panel has
/// to exist before there is a screen to configure it from, and this keeps the
/// choice in one place until that screen lands.
pub fn shortcut_spec() -> String {
    std::env::var("COURIER_QUICKVIEW_SHORTCUT").unwrap_or_else(|_| DEFAULT_SHORTCUT.into())
}

/// Frost the window behind the page.
///
/// `backdrop-filter` in CSS blurs what is behind an element *within the
/// document*. Blurring the desktop behind the window is a native material, so
/// it is applied to the window and the page is left transparent on top of it.
pub fn frost(window: &WebviewWindow) {
    #[cfg(target_os = "macos")]
    {
        use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial, NSVisualEffectState};
        // HudWindow is the material the system's own floating panels use, and
        // `Active` keeps it frosted even when the panel does not hold focus --
        // which is the normal case here, since it opens over another app.
        let _ = apply_vibrancy(
            window,
            NSVisualEffectMaterial::HudWindow,
            Some(NSVisualEffectState::Active),
            Some(12.0),
        );
    }
    #[cfg(target_os = "windows")]
    {
        let _ = window_vibrancy::apply_acrylic(window, Some((18, 18, 22, 125)));
    }
}

/// Show the panel, or put it away if it is already up.
///
/// Toggling rather than only showing is what makes the shortcut a switch: the
/// same key that summoned it dismisses it, so the panel never has to be aimed
/// at to be closed.
pub fn toggle(app: &AppHandle) {
    let Some(window) = app.get_webview_window(LABEL) else {
        eprintln!("[quickview] no window labelled {LABEL}");
        return;
    };

    if window.is_visible().unwrap_or(false) {
        let _ = window.hide();
        return;
    }

    // Deliberately *not* centred here. Centring on every show is the one line
    // that makes a movable window pointless: drag the panel where you want it,
    // dismiss it, summon it again, and it is back in the middle of the screen
    // having thrown your decision away. It is centred once, by
    // `tauri.conf.json`, and after that where it sits is the reader's business
    // -- `tauri-plugin-window-state` remembers it across restarts.
    let _ = window.show();
    let _ = window.set_focus();
    // The page decides what "opened" means -- a fresh conversation, the input
    // focused -- because that is a product question, not a windowing one.
    let _ = window.emit_to(LABEL, "quickview://opened", ());
}

/// Register the global shortcut. Returns the spec on success.
///
/// A failure here is reported and swallowed. The rest of the app works without
/// the panel, and taking the whole thing down because a key combination was
/// already spoken for would be a poor trade -- but it is printed, because a
/// hotkey that was never registered and a hotkey that does nothing look
/// identical from the keyboard.
pub fn register(app: &AppHandle) -> Option<String> {
    let spec = shortcut_spec();
    let shortcut: Shortcut = match spec.parse() {
        Ok(parsed) => parsed,
        Err(err) => {
            eprintln!("[quickview] {spec:?} is not a shortcut I can parse: {err}");
            return None;
        }
    };

    let handle = app.clone();
    let outcome = app.global_shortcut().on_shortcut(shortcut, move |_, _, event| {
        // Press only. Without this the panel toggles twice per keypress -- once
        // down, once up -- and appears to ignore the shortcut entirely.
        if event.state() == ShortcutState::Pressed {
            toggle(&handle);
        }
    });

    match outcome {
        Ok(()) => Some(spec),
        Err(err) => {
            eprintln!(
                "[quickview] could not register {spec:?}: {err}\n\
                 [quickview] on macOS Ctrl+Space is the system shortcut for \
                 'Select the previous input source'. Either turn that off in \
                 System Settings > Keyboard > Keyboard Shortcuts > Input Sources, \
                 or set COURIER_QUICKVIEW_SHORTCUT to something else."
            );
            None
        }
    }
}

/// Prepare the window at startup: frost it, and teach it to put itself away.
///
/// It used to dismiss itself the moment it lost focus, the way a menu does.
/// That is the right behaviour for a panel you cannot move, and the wrong one
/// the moment you can: arranging a window means clicking on things that are not
/// it -- the desktop, the app underneath, the other edge of the screen -- and
/// every one of those made it vanish mid-drag. Now it goes away when it is
/// asked to: Escape, the shortcut again, or the close button.
pub fn setup(app: &AppHandle) {
    let Some(window) = app.get_webview_window(LABEL) else {
        return;
    };
    frost(&window);

    // The close button hides rather than destroys. Destroying it would take the
    // webview with it, so the next summon would pay for a fresh page load --
    // and the whole point of the panel is that it is already there.
    let hidden = window.clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = hidden.hide();
        }
    });
}
