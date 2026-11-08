import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ToastProvider } from './components/ui/toast'
import '@xyflow/react/dist/style.css'
import './styles.css'
import './home.css'
import './effects.css'
import './loading.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><ToastProvider><App /></ToastProvider></React.StrictMode>,
)

