import { contextBridge, ipcRenderer, webUtils } from 'electron'

function on<T>(channel: string, cb: (payload: T) => void): () => void {
  const handler = (_e: unknown, payload: T): void => cb(payload)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api = {
  backend: {
    get: () => ipcRenderer.invoke('backend:get'),
    restart: () => ipcRenderer.invoke('backend:restart'),
    onStatus: (cb: (s: unknown) => void) => on('backend:status', cb),
    onLog: (cb: (line: string) => void) => on('backend:log', cb)
  },
  settings: {
    get: () => ipcRenderer.invoke('settings:get'),
    set: (patch: Record<string, unknown>) => ipcRenderer.invoke('settings:set', patch)
  },
  dialogs: {
    audio: (): Promise<string[]> => ipcRenderer.invoke('dialog:audio'),
    python: (): Promise<string | null> => ipcRenderer.invoke('dialog:python'),
    folder: (title: string): Promise<string | null> => ipcRenderer.invoke('dialog:folder', title)
  },
  shell: {
    openPath: (p: string) => ipcRenderer.invoke('shell:openPath', p),
    showItem: (p: string) => ipcRenderer.invoke('shell:showItem', p),
    external: (url: string) => ipcRenderer.invoke('shell:external', url)
  },
  setup: {
    run: (): Promise<boolean> => ipcRenderer.invoke('setup:run'),
    onEvent: (cb: (e: unknown) => void) => on('setup:event', cb)
  },
  // Electron no longer exposes File.path; dropped files need this to get a real path.
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  info: () => ipcRenderer.invoke('app:info')
}

contextBridge.exposeInMainWorld('songbreak', api)

export type SongbreakApi = typeof api
