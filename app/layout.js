import './globals.css'
import { Providers } from './providers'

export const metadata = {
  title: 'HCGameLoc — консоль продюсера',
  description: 'Консоль продюсера платформы локализации HCGameLoc',
}

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        <script dangerouslySetInnerHTML={{__html:'window.addEventListener("error",function(e){if(e.error instanceof DOMException&&e.error.name==="DataCloneError"&&e.message&&e.message.includes("PerformanceServerTiming")){e.stopImmediatePropagation();e.preventDefault()}},true);'}} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
