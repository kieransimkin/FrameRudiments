import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {resolve} from 'node:path';
// A dedicated static entry: do not publish the native /api/* editor on Pages.
export default defineConfig({
  plugins:[react()], base:'/FrameRudiments/timeline/',
  build:{outDir:'dist-timeline',emptyOutDir:true,sourcemap:false,
         rollupOptions:{input:resolve(process.cwd(),'timeline-gallery.html')}},
});
