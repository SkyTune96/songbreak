import { app, BrowserWindow, dialog, ipcMain, nativeTheme, shell } from 'electron'
import { join } from 'path'
import { backend } from './backend'
import { loadSettings, managedPython, saveSettings, type Settings } from './settings'
import { runSetup } from './setup'

const AUDIO_EXTENSIONS = ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'wma', 'aiff', 'aif', 'mp4', 'webm', 'mkv']
const isDev = !app.isPackaged && !!process.env.ELECTRON_RENDERER_URL

let win: BrowserWindow | null = null

function createWindow(): void {
  nativeTheme.themeSource = 'dark'
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#0e0f13',
    title: 'Songbreak',
    icon: join(__dirname, '../../build/icon.png'),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true
    }
  })
  win.once('ready-to-show', () => win?.show())
  // Links (e.g. GitHub, Ollama) open in the real browser, never inside the app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e) => e.preventDefault())
  if (isDev) win.loadURL(process.env.ELECTRON_RENDERER_URL!)
  else win.loadFile(join(__dirname, '../renderer/index.html'))
}

function send(channel: string, payload: unknown): void {
  win?.webContents.send(channel, payload)
}

backend.on('status', (s) => send('backend:status', s))
backend.on('log', (line) => send('backend:log', line))

ipcMain.handle('backend:get', () => backend.status)
ipcMain.handle('backend:restart', () => backend.start())

ipcMain.handle('settings:get', () => ({ ...loadSettings(), managedPython: managedPython() }))
ipcMain.handle('settings:set', (_e, patch: Partial<Settings>) => {
  const before = loadSettings()
  const next = saveSettings(patch)
  // The server is bound to one library and interpreter; restart it if either changed.
  if (before.pythonPath !== next.pythonPath || before.libraryDir !== next.libraryDir) backend.start()
  return next
})

ipcMain.handle('dialog:audio', async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: 'Add songs',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Audio', extensions: AUDIO_EXTENSIONS }]
  })
  return r.canceled ? [] : r.filePaths
})

ipcMain.handle('dialog:python', async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: 'Choose python.exe from a Songbreak environment',
    properties: ['openFile'],
    filters: [{ name: 'Python', extensions: process.platform === 'win32' ? ['exe'] : ['*'] }]
  })
  return r.canceled ? null : r.filePaths[0]
})

ipcMain.handle('dialog:folder', async (_e, title: string) => {
  const r = await dialog.showOpenDialog(win!, { title, properties: ['openDirectory', 'createDirectory'] })
  return r.canceled ? null : r.filePaths[0]
})

ipcMain.handle('shell:openPath', (_e, p: string) => shell.openPath(p))
ipcMain.handle('shell:showItem', (_e, p: string) => shell.showItemInFolder(p))
ipcMain.handle('shell:external', (_e, url: string) => {
  if (/^https?:\/\//.test(url)) shell.openExternal(url)
})
ipcMain.handle('app:info', () => ({ version: app.getVersion(), platform: process.platform }))

ipcMain.handle('setup:run', async () => {
  backend.stop()
  const ok = await runSetup((e) => send('setup:event', e))
  if (ok) {
    saveSettings({ pythonPath: null })
    backend.start()
  }
  return ok
})

app.whenReady().then(() => {
  createWindow()
  backend.start()
})

app.on('window-all-closed', () => {
  backend.stop()
  app.quit()
})
app.on('before-quit', () => backend.stop())
