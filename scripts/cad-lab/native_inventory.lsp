;; Read-only database inventory. Counts definitions, NOT expanded instances.
;; Usage: (ga:inventory "/absolute/fresh-output.tsv")
;; No entmod, explode, save, settings changes, or network access.
(defun ga:inventory (path / *error* stream block entity data count blocks xrefs)
  (defun *error* (message)
    (if stream (close stream))
    (princ (strcat "\nGA inventory failed: " message)) (princ))
  (if (findfile path)
    (princ "\nGA refuses to overwrite an existing report.")
    (progn
      (setq stream (open path "w" "utf8") count 0 blocks 0 xrefs 0)
      (if stream
        (progn
          (write-line "record\towner\thandle\ttype\tlayer\treference" stream)
          (setq block (tblnext "BLOCK" T))
          (while block
            (setq blocks (1+ blocks))
            (if (/= 0 (logand 4 (cdr (assoc 70 block)))) (setq xrefs (1+ xrefs)))
            (write-line (strcat "block\t" (cdr (assoc 2 block)) "\t\t"
              (itoa (cdr (assoc 70 block))) "\t\t"
              (if (assoc 1 block) (cdr (assoc 1 block)) "")) stream)
            (setq entity (cdr (assoc -2 block)))
            (while entity
              (setq data (entget entity))
              (if (and data (/= "ENDBLK" (cdr (assoc 0 data))))
                (progn
                  (setq count (1+ count))
                  (write-line (strcat "entity\t" (cdr (assoc 2 block)) "\t"
                    (if (assoc 5 data) (cdr (assoc 5 data)) "") "\t"
                    (cdr (assoc 0 data)) "\t"
                    (if (assoc 8 data) (cdr (assoc 8 data)) "") "\t"
                    (if (= "INSERT" (cdr (assoc 0 data))) (cdr (assoc 2 data)) "")) stream)))
              (setq entity (entnext entity)))
            (setq block (tblnext "BLOCK")))
          (close stream) (setq stream nil)
          (princ (strcat "\nGA inventory: " (itoa count) " entities in "
            (itoa blocks) " definitions; " (itoa xrefs) " XREF definitions.")))
        (princ "\nGA cannot open output file."))))
  (princ))
(princ "\nGreen Atlas read-only inventory loaded. Use (ga:inventory path).")
(princ)
