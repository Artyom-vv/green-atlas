---
title: Native CAD extraction и admission
type: module
status: implemented
last_verified: 2026-09-20
code_paths:
  - apps/api/app/cad_intake
  - apps/api/app/cad_import
  - apps/api/app/dxf_import
  - tools/autocad-bridge
---

# Native CAD extraction и admission

## Назначение

Открыть CAD-комплект AutoCAD только на чтение, получить доказуемую геометрию и допустить её без fallback parser.

## Пользовательское взаимодействие

Оператор передаёт сохранённый CAD-комплект из AutoCAD. Доступная геометрия публикуется с coverage; остаток приходит как компактные замечания и может быть принят для дальнейшей работы по доступному scope. Extraction не назначает предметные роли слоёв.

## Как работает

- ObjectARX `0.1.33` использует отдельную очередь `v033` и отвергает несовпадающую версию;
- root DWG/DXF и детерминированные package-local XREF открываются отдельными read-only `AcDbDatabase`;
- authored XREF transform, SHA-256 и namespace `xref|layer` сохраняются;
- evidence содержит REGION, supported HATCH area, finite paths, points, blocks/MINSERT и coverage;
- compiler проверяет protocol, counts, topology, closure, Z span, area/perimeter и hashes;
- `cad_intake` связывает snapshot с комплектом; rejected probe остаётся issue, а не поводом вызвать второй parser.

## Преимущества

- source identity и XREF provenance проверяются до использования;
- 7 остаточных instances Кустанайской не скрыты и не блокируют admission;
- document-interactive `acdbResolveCurrentXRefs` не вызывается для side database на Mac;
- нет повторной записи или повторного DXF-чтения в native тракте.

## Ограничения

**Проверено:** AutoCAD 2027.0.1/macOS, Кустанайская, 101829 source instances; 79625 native calculation, 22197 context, 7 unresolved; 2/2 XREF; topology passed. **Реализовано, не проверено E2E:** desktop delivery и дальнейший user flow. **План:** Windows, notarization, matrix улиц и multi-surface HATCH. **Историческое:** ezdxf/повторная DXF-конвертация — не production source of truth.
