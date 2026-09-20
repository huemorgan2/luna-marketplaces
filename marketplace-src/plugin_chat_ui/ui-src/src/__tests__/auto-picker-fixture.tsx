import { createRoot } from 'react-dom/client'
import { ComposerModelSelect } from '../views/ChatPanel'
import '../index.css'

createRoot(document.getElementById('root')!).render(
  <main className="min-h-screen bg-ink-950 text-ink-100 font-sans p-10">
    <p className="text-luna-300 text-sm">LUNA · LOCAL UI VERIFICATION</p>
    <h1 className="text-2xl mt-3">Choose how Luna answers</h1>
    <p className="text-ink-400 mt-3">AUTO selects a model for each message. A manual choice stays in your control.</p>
    <div className="fixed left-10 bottom-10"><ComposerModelSelect /></div>
  </main>,
)
