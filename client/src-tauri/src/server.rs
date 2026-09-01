//! Starting, adopting, and stopping the Python server.
//!
//! The rule is *adopt before spawn*. A reader who already has `./run.sh` going
//! should get that server, not a second one fighting it for port 8080 and
//! running migrations against the same SQLite file. So this probes the port
//! first and only starts something if nothing answers.
//!
//! This is not the reasoning `run.sh` applies to Ollama, and deliberately so.
//! Ollama is a shared system service with its own lifecycle, which is why
//! nothing here tries to manage it. The Python server is Courier's own, one
//! per device under the current architecture, and owning it is the whole point
//! of shipping a desktop app rather than a bookmark.

use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// Where the server listens. `run.sh --port` and `BIND_PORT` can both move it,
/// so this is read from the environment with `config.py`'s default rather than
/// baked in -- a shell that hardcoded 8080 would adopt the wrong server, or
/// spawn a second one alongside a first that had simply moved.
pub fn port() -> u16 {
    std::env::var("BIND_PORT")
        .ok()
        .and_then(|raw| raw.trim().parse().ok())
        .unwrap_or(8080)
}

/// How long to wait for a server we started to begin answering. Generous: the
/// first import of numpy and fastapi on a cold page cache is not quick, and
/// giving up early would show the gate an error about a server that was about
/// to work.
const READY_TIMEOUT: Duration = Duration::from_secs(25);

/// The child we spawned, if we spawned one. `None` means we adopted a server
/// somebody else started -- and an adopted server must never be killed on
/// exit, because we did not start it and it may outlive this window on purpose.
#[derive(Default)]
pub struct ManagedServer(pub Mutex<Option<CommandChild>>);

fn address() -> SocketAddr {
    ([127, 0, 0, 1], port()).into()
}

/// Whether something is already listening. A TCP connect rather than an HTTP
/// GET on /healthz: at this point any listener at all means "do not spawn a
/// second one", and asking for a response would need an HTTP client in the
/// shell crate for no extra information.
pub fn is_listening() -> bool {
    TcpStream::connect_timeout(&address(), Duration::from_millis(300)).is_ok()
}

/// Block until the port answers, or the timeout expires. Returns whether it
/// came up.
fn wait_until_ready() -> bool {
    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        if is_listening() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

/// The interpreter to run, and the arguments that start the server with it.
///
/// In a debug build this is the repo's own virtualenv, located from the crate
/// path baked in at compile time -- which is exactly right for development and
/// exactly wrong for a build somebody else installs.
///
/// A release build looks for a sidecar binary bundled beside the executable.
/// That binary does not exist yet; until it does, a release build adopts a
/// running server and otherwise leaves the gate to say nothing is there. That
/// is an honest failure rather than a mysterious one.
fn interpreter() -> Option<(String, Vec<String>)> {
    if let Ok(explicit) = std::env::var("COURIER_SERVER_BIN") {
        return Some((explicit, vec!["-m".into(), "app".into()]));
    }

    #[cfg(debug_assertions)]
    {
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent() // client/
            .and_then(|p| p.parent())?; // repo root
        let python = repo.join(".venv/bin/python");
        if python.exists() {
            return Some((
                python.to_string_lossy().into_owned(),
                vec!["-m".into(), "app".into()],
            ));
        }
        None
    }

    #[cfg(not(debug_assertions))]
    {
        None
    }
}

/// Bring a server up if there isn't one, and report whether the API is
/// answering by the time this returns.
///
/// Called off the main thread: it blocks for as long as the server takes to
/// boot, and doing that in `setup` would freeze the app with no window on
/// screen to explain why.
pub fn ensure_running(app: &AppHandle) -> bool {
    if is_listening() {
        println!("[server] already running on :{} -- adopting it", port());
        return true;
    }

    let Some((program, args)) = interpreter() else {
        eprintln!(
            "[server] nothing listening on :{} and no interpreter to start one. \
             Start it yourself, or point the app at another host from the gate.",
            port()
        );
        return false;
    };

    println!("[server] starting {program} -m app");
    let spawned = app
        .shell()
        .command(&program)
        .args(&args)
        // Opt the child into its own orphan watchdog. `shutdown` below covers
        // a clean quit, but nothing in this process runs on SIGKILL, so the
        // server has to be able to notice on its own that we are gone.
        .env("COURIER_EXIT_WITH_PARENT", "1")
        // The rest of the environment is inherited, so a reader who exported
        // OLLAMA_URL or GOOGLE_CLIENT_ID before launching gets what they meant.
        .spawn();

    match spawned {
        Ok((mut events, child)) => {
            // The server's stdout is where the token, the migrations and every
            // skill error go. Dropping it silently would make a failed boot
            // completely opaque, so it is forwarded to this process's log.
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_shell::process::CommandEvent;
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            print!("[server] {}", String::from_utf8_lossy(&line))
                        }
                        CommandEvent::Stderr(line) => {
                            eprint!("[server] {}", String::from_utf8_lossy(&line))
                        }
                        CommandEvent::Terminated(status) => {
                            eprintln!("[server] exited: {:?}", status.code)
                        }
                        _ => {}
                    }
                }
            });

            if let Some(state) = app.try_state::<ManagedServer>() {
                *state.0.lock().unwrap() = Some(child);
            }

            let ready = wait_until_ready();
            if !ready {
                eprintln!("[server] did not answer within {READY_TIMEOUT:?}");
            }
            ready
        }
        Err(err) => {
            eprintln!("[server] could not start {program}: {err}");
            false
        }
    }
}

/// Stop the server, but only if we were the ones who started it.
///
/// Called on app exit. Without this, force-quitting the window leaves a Python
/// process holding :8080, and the next launch adopts a server the reader
/// thinks they closed -- which is the confusing half of the orphan problem,
/// worse than the leaked memory.
pub fn shutdown(app: &AppHandle) {
    if let Some(state) = app.try_state::<ManagedServer>() {
        if let Some(child) = state.0.lock().unwrap().take() {
            println!("[server] stopping the server we started");
            let _ = child.kill();
        }
    }
}
