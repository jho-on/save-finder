# SaveFinder Android

Aplicativo Android do SaveFinder: React Native + TypeScript.

## Requisitos

- Node.js >= 22.11 (ver `package.json` → `engines`)
- npm
- Para compilar/executar no Android:
  - JDK 17+
  - Android SDK (plataformas e build-tools), `ANDROID_HOME` configurado
  - Emulador Android ou dispositivo físico com depuração USB
  - Veja o guia oficial de ambiente: <https://reactnative.dev/docs/set-up-environment>

## Desenvolvimento

```bash
npm install        # instalar dependências
npm start          # Metro bundler
npm run android    # compilar e instalar no emulador/dispositivo
npm test           # testes (jest)
npm run lint       # lint
```

## Estrutura

```text
App.tsx            componente raiz
index.js           registro do app
android/           projeto nativo Android (Gradle)
ios/               projeto nativo iOS (gerado pelo template; fora de escopo)
__tests__/         testes
```
