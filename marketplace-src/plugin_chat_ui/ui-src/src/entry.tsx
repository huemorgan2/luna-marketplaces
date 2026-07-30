/**
 * IIFE entry: assigns window.LunaChatUI = { ChatPanel } (via Vite lib mode's
 * `name: 'LunaChatUI'`). The shell's RemoteChatPanel renders ChatPanel with
 * the same props core's chat mount has always used.
 */
import './index.css'

export { ChatPanel } from './views/ChatPanel'
