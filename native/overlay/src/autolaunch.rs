//! One-shot CLI support for registering the app with SteamVR's application list
//! and toggling its auto-launch flag ("start with SteamVR").
//!
//! Invoked as:
//!   PuriPulyHeartOverlay --set-autolaunch <manifest_path> <app_key> <0|1>
//!
//! Exit codes: 0 = ok, 2 = usage, 3 = SteamVR not running / init failed,
//! 4 = OpenVR applications API error.

#[cfg(windows)]
pub fn run_set_autolaunch(manifest_path: &str, app_key: &str, enable: bool) -> i32 {
    use std::ffi::{CStr, CString};
    use std::os::raw::c_char;

    let manifest_c = match CString::new(manifest_path) {
        Ok(value) => value,
        Err(_) => {
            eprintln!("[autolaunch][ERROR] manifest path contains NUL");
            return 2;
        }
    };
    let app_key_c = match CString::new(app_key) {
        Ok(value) => value,
        Err(_) => {
            eprintln!("[autolaunch][ERROR] app key contains NUL");
            return 2;
        }
    };

    // VRApplication_Utility connects to a running SteamVR but does not start one —
    // registration is only possible while SteamVR is up, and we report that clearly.
    let mut init_error = openvr_sys::EVRInitError_VRInitError_None;
    unsafe {
        openvr_sys::VR_InitInternal(
            &mut init_error,
            openvr_sys::EVRApplicationType_VRApplication_Utility,
        );
    }
    if init_error != openvr_sys::EVRInitError_VRInitError_None {
        eprintln!("[autolaunch][ERROR] SteamVR is not running (VR_InitInternal={init_error})");
        return 3;
    }

    let result = (|| -> Result<(), String> {
        // Build "FnTable:IVRApplications_XXX" from the bindgen version constant.
        let interface_version = CStr::from_bytes_with_nul(openvr_sys::IVRApplications_Version)
            .map_err(|error| format!("invalid IVRApplications version constant: {error}"))?;
        let fn_table_name = CString::new(format!("FnTable:{}", interface_version.to_string_lossy()))
            .map_err(|error| format!("invalid interface name: {error}"))?;

        let mut interface_error = openvr_sys::EVRInitError_VRInitError_None;
        let applications_api = unsafe {
            openvr_sys::VR_GetGenericInterface(fn_table_name.as_ptr(), &mut interface_error)
        } as *mut openvr_sys::VR_IVRApplications_FnTable;
        if applications_api.is_null()
            || interface_error != openvr_sys::EVRInitError_VRInitError_None
        {
            return Err(format!(
                "VR_GetGenericInterface(IVRApplications) failed: {interface_error}"
            ));
        }

        let add_manifest = unsafe { (*applications_api).AddApplicationManifest }
            .ok_or("AddApplicationManifest unavailable")?;
        let remove_manifest = unsafe { (*applications_api).RemoveApplicationManifest }
            .ok_or("RemoveApplicationManifest unavailable")?;
        let set_auto_launch = unsafe { (*applications_api).SetApplicationAutoLaunch }
            .ok_or("SetApplicationAutoLaunch unavailable")?;

        if enable {
            let add_error =
                unsafe { add_manifest(manifest_c.as_ptr() as *mut c_char, false) };
            if add_error != openvr_sys::EVRApplicationError_VRApplicationError_None {
                return Err(format!("AddApplicationManifest failed: {add_error}"));
            }
            let set_error =
                unsafe { set_auto_launch(app_key_c.as_ptr() as *mut c_char, true) };
            if set_error != openvr_sys::EVRApplicationError_VRApplicationError_None {
                return Err(format!("SetApplicationAutoLaunch(true) failed: {set_error}"));
            }
        } else {
            // Best-effort: clearing the flag can fail if the app was never registered —
            // that's fine, removal below is what actually matters.
            let _ = unsafe { set_auto_launch(app_key_c.as_ptr() as *mut c_char, false) };
            let _ = unsafe { remove_manifest(manifest_c.as_ptr() as *mut c_char) };
        }
        Ok(())
    })();

    unsafe {
        openvr_sys::VR_ShutdownInternal();
    }

    match result {
        Ok(()) => {
            println!("{{\"autolaunch\": {enable}}}");
            0
        }
        Err(message) => {
            eprintln!("[autolaunch][ERROR] {message}");
            4
        }
    }
}

#[cfg(not(windows))]
pub fn run_set_autolaunch(_manifest_path: &str, _app_key: &str, _enable: bool) -> i32 {
    eprintln!("[autolaunch][ERROR] SteamVR auto-launch registration is Windows-only");
    2
}
