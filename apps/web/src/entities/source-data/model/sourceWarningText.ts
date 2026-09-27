/** Historic snapshots keep their original diagnostic; don't repeat obsolete UI policy. */
export function sourceWarningText(warning: string): string {
  const marker =
    ' Если такой слой назначен физическим ограничением, расчёт будет остановлен;';
  const index = warning.indexOf(marker);
  return index < 0
    ? warning
    : warning.slice(0, index) +
        ' Пропущенные объекты не участвуют в проверках. Ниже можно разрешить расчёт по доступным данным.';
}
