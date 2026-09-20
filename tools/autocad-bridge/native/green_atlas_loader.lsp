(defun green-atlas-load-native-bridge (/ loader-file loader-root module-path)
  (setq loader-file (findfile "green_atlas_loader.lsp"))
  (if loader-file
    (progn
      (setq loader-root (vl-filename-directory loader-file))
      (setq module-path
        (strcat loader-root "/MacOS/GreenAtlasBridge.bundle"))
      (vl-catch-all-apply 'arxload (list module-path))
    )
  )
  (princ)
)

(green-atlas-load-native-bridge)
