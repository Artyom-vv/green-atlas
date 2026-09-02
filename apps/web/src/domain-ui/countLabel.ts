export function countLabel(value: number, one: string, few: string, many: string) {
  const modulo100 = Math.abs(value) % 100;
  const modulo10 = Math.abs(value) % 10;
  if (modulo100 >= 11 && modulo100 <= 14) return `${value} ${many}`;
  if (modulo10 === 1) return `${value} ${one}`;
  if (modulo10 >= 2 && modulo10 <= 4) return `${value} ${few}`;
  return `${value} ${many}`;
}

export const plantingCount = (value: number) => countLabel(value, 'посадка', 'посадки', 'посадок');
