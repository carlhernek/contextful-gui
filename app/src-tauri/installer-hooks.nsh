; Contextful NSIS installer hooks (see tauri.conf.json bundle.windows.nsis.installerHooks).
; Ensures a prior install is removed before copying files on version bumps, even when
; the user chose not to uninstall on the maintenance page.

!macro NSIS_HOOK_PREINSTALL
  ReadRegStr $R0 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R0 == ""
    Goto cf_preinstall_done
  ${EndIf}

  ; Only run when a previous version is registered (reinstall / upgrade / downgrade).
  ReadRegStr $R1 SHCTX "${UNINSTKEY}" "DisplayVersion"
  ${If} $R1 == ""
    Goto cf_preinstall_done
  ${EndIf}

  nsis_tauri_utils::SemverCompare "${VERSION}" $R1
  Pop $R2
  ; $R2: 1 = upgrading, 0 = same, -1 = downgrading — clean in all cases.
  ${If} $R2 = 1
    Goto cf_force_uninstall
  ${ElseIf} $R2 = 0
    Goto cf_force_uninstall
  ${ElseIf} $R2 = -1
    Goto cf_force_uninstall
  ${Else}
    Goto cf_preinstall_done
  ${EndIf}

  cf_force_uninstall:
    ReadRegStr $R3 SHCTX "${MANUPRODUCTKEY}" ""
    ${If} $R3 == ""
      ReadRegStr $R3 SHCTX "${UNINSTKEY}" "InstallLocation"
    ${EndIf}
    ${If} $R3 == ""
      StrCpy $R3 "$INSTDIR"
    ${EndIf}

    ; Skip if maintenance page already removed the old tree.
    IfFileExists "$R3\uninstall.exe" 0 cf_preinstall_done

    !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
    !insertmacro CheckIfAppIsRunning "contextful-sidecar.exe" "${PRODUCTNAME}"

    ClearErrors
    StrCpy $R4 "$R0 /S _?=$R3"
    ExecWait '$R4' $R5
    ${If} $R5 <> 0
      DetailPrint "Contextful: cleanup of previous install exited with code $R5."
    ${EndIf}

  cf_preinstall_done:
!macroend
