const BINARY_DXF_SIGNATURE = 'AutoCAD Binary DXF\r\n\u001a\u0000';

/** Read only the signature, cancelling a non-Range response before its full body. */
export async function assertAsciiDxf(
  url: string,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    headers: { Range: `bytes=0-${BINARY_DXF_SIGNATURE.length - 1}` },
    signal,
  });
  if (!response.ok || !response.body) {
    await response.body?.cancel().catch(() => undefined);
    throw new Error(
      `Не удалось прочитать CAD-источник (HTTP ${response.status}).`,
    );
  }
  const reader = response.body.getReader();
  let prefix = '';
  try {
    while (prefix.length < BINARY_DXF_SIGNATURE.length) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const byte of value.subarray(
        0,
        BINARY_DXF_SIGNATURE.length - prefix.length,
      )) {
        prefix += String.fromCharCode(byte);
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  signal.throwIfAborted();
  if (prefix === BINARY_DXF_SIGNATURE) {
    throw new Error(
      'Для CAD-отображения необходим ASCII DXF; получен бинарный DXF.',
    );
  }
}
