export interface GpuQueryAdapter {
  api: 'webgl1' | 'webgl2';
  create: () => WebGLQuery | null;
  begin: (query: WebGLQuery) => void;
  end: () => void;
  isActive: () => boolean;
  isDisjoint: () => boolean;
  available: (query: WebGLQuery) => boolean;
  nanoseconds: (query: WebGLQuery) => number;
  remove: (query: WebGLQuery) => void;
}

interface TimerExtension {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

interface WebGl1TimerExtension extends TimerExtension {
  CURRENT_QUERY_EXT: number;
  QUERY_RESULT_EXT: number;
  QUERY_RESULT_AVAILABLE_EXT: number;
  createQueryEXT: () => WebGLQuery | null;
  beginQueryEXT: (target: number, query: WebGLQuery) => void;
  endQueryEXT: (target: number) => void;
  getQueryEXT: (target: number, parameter: number) => unknown;
  getQueryObjectEXT: (query: WebGLQuery, parameter: number) => unknown;
  deleteQueryEXT: (query: WebGLQuery) => void;
}

export function createGpuQueryAdapter(
  gl: WebGLRenderingContext | WebGL2RenderingContext,
): GpuQueryAdapter | null {
  if ('createQuery' in gl) {
    const ext = gl.getExtension(
      'EXT_disjoint_timer_query_webgl2',
    ) as TimerExtension | null;
    if (ext)
      return {
        api: 'webgl2',
        create: () => gl.createQuery(),
        begin: (query) => gl.beginQuery(ext.TIME_ELAPSED_EXT, query),
        end: () => gl.endQuery(ext.TIME_ELAPSED_EXT),
        isActive: () =>
          Boolean(gl.getQuery(ext.TIME_ELAPSED_EXT, gl.CURRENT_QUERY)),
        isDisjoint: () => Boolean(gl.getParameter(ext.GPU_DISJOINT_EXT)),
        available: (query) =>
          Boolean(gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)),
        nanoseconds: (query) =>
          Number(gl.getQueryParameter(query, gl.QUERY_RESULT)),
        remove: (query) => gl.deleteQuery(query),
      };
  }
  const ext = gl.getExtension(
    'EXT_disjoint_timer_query',
  ) as WebGl1TimerExtension | null;
  if (!ext) return null;
  return {
    api: 'webgl1',
    create: () => ext.createQueryEXT(),
    begin: (query) => ext.beginQueryEXT(ext.TIME_ELAPSED_EXT, query),
    end: () => ext.endQueryEXT(ext.TIME_ELAPSED_EXT),
    isActive: () =>
      Boolean(ext.getQueryEXT(ext.TIME_ELAPSED_EXT, ext.CURRENT_QUERY_EXT)),
    isDisjoint: () => Boolean(gl.getParameter(ext.GPU_DISJOINT_EXT)),
    available: (query) =>
      Boolean(ext.getQueryObjectEXT(query, ext.QUERY_RESULT_AVAILABLE_EXT)),
    nanoseconds: (query) =>
      Number(ext.getQueryObjectEXT(query, ext.QUERY_RESULT_EXT)),
    remove: (query) => ext.deleteQueryEXT(query),
  };
}
