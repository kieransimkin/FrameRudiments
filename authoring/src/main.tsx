import React from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './App.tsx';
import 'react-timeline-sequence/styles.css';
import './styles.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
