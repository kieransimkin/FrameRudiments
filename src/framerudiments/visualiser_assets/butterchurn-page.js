// Version-specific host bridge for Butterchurn 2.6.7. The engine owns all effects.
async function initialise(config) {
  let randomState = config.seed >>> 0;
  Math.random = () => { randomState = (Math.imul(1664525, randomState) + 1013904223) >>> 0; return randomState / 4294967296; };
  const canvas = document.createElement('canvas');
  canvas.width = config.width; canvas.height = config.height;
  document.body.appendChild(canvas);
  const engine = window.butterchurn.default || window.butterchurn;
  const viz = engine.createVisualizer(null, canvas, {
    width: config.width, height: config.height, pixelRatio: 1, textureRatio: 1,
    meshWidth: config.mesh_width, meshHeight: config.mesh_height,
  });
  const r = viz.renderer, gl = r.gl;
  for (const name of ['targetFrameBuffer', 'targetTexture', 'calcTimeAndFPS']) {
    if (!r[name]) throw new Error(`Pinned Butterchurn bridge missing ${name}`);
  }
  const errors = [];
  canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); errors.push('WebGL context lost'); });
  // Capture shader failures instead of allowing Butterchurn to log and continue.
  const linkProgram = gl.linkProgram.bind(gl);
  gl.linkProgram = program => {
    linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) errors.push(gl.getProgramInfoLog(program));
  };
  if (config.extraImages && Object.keys(config.extraImages).length) {
    const images = {};
    await Promise.all(Object.entries(config.extraImages).map(([name, url]) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => { images[name] = {data:url, width:image.naturalWidth, height:image.naturalHeight}; resolve(); };
      image.onerror = () => reject(new Error(`Extra texture failed: ${name}`)); image.src = url;
    })));
    viz.loadExtraImages(images);
    const deadline = performance.now() + 10000;
    while (Object.keys(images).some(name => !r.image.samplers[name])) {
      if (performance.now() > deadline) throw new Error('Extra texture GPU upload timeout');
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  }
  viz.loadPreset(config.preset, 0);
  if (errors.length) throw new Error(errors.join('\n'));
  let clock = 0;
  r.calcTimeAndFPS = function () {
    this.time = clock; this.fps = config.fps;
    // No transition or automatic preset switch; no RAF or wall-clock smoothing.
    this.blending = false;
  };
  function shader(kind, source) {
    const item = gl.createShader(kind); gl.shaderSource(item, source); gl.compileShader(item);
    if (!gl.getShaderParameter(item, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(item));
    return item;
  }
  const vertex = shader(gl.VERTEX_SHADER, `#version 300 es
    out vec2 uv; void main(){ vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));
    uv=p; gl_Position=vec4(p*2.0-1.0,0.0,1.0); }`);
  const fragment = shader(gl.FRAGMENT_SHADER, `#version 300 es
    precision highp float; in vec2 uv; uniform sampler2D source; uniform float amount;
    out vec4 colour; void main(){ vec4 pixel=texture(source,uv); colour=vec4(pixel.rgb,pixel.a*amount); }`);
  const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  if (errors.length) throw new Error(errors.join('\n'));
  const vao = gl.createVertexArray(), texture = gl.createTexture();
  const amount = gl.getUniformLocation(program, 'amount'), source = gl.getUniformLocation(program, 'source');
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const decode = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
  function encode(bytes) {
    let text = ''; for (let offset = 0; offset < bytes.length; offset += 32768)
      text += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    return btoa(text);
  }
  window.renderFrame = message => {
    if (errors.length) throw new Error(errors.join('\n'));
    clock = message.time;
    const state = {
      program: gl.getParameter(gl.CURRENT_PROGRAM), vao: gl.getParameter(gl.VERTEX_ARRAY_BINDING),
      fbo: gl.getParameter(gl.FRAMEBUFFER_BINDING), viewport: gl.getParameter(gl.VIEWPORT),
      active: gl.getParameter(gl.ACTIVE_TEXTURE), blend: gl.isEnabled(gl.BLEND),
      srcRGB: gl.getParameter(gl.BLEND_SRC_RGB), dstRGB: gl.getParameter(gl.BLEND_DST_RGB),
      srcA: gl.getParameter(gl.BLEND_SRC_ALPHA), dstA: gl.getParameter(gl.BLEND_DST_ALPHA),
      eqRGB: gl.getParameter(gl.BLEND_EQUATION_RGB), eqA: gl.getParameter(gl.BLEND_EQUATION_ALPHA),
    };
    gl.activeTexture(gl.TEXTURE0);
    const oldTexture = gl.getParameter(gl.TEXTURE_BINDING_2D), oldSampler = gl.getParameter(gl.SAMPLER_BINDING);
    gl.bindSampler(0, null); gl.bindTexture(gl.TEXTURE_2D, texture);
    const inject = (rgba, opacity) => {
      if (rgba) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, config.width, config.height, 0,
        gl.RGBA, gl.UNSIGNED_BYTE, decode(rgba));
      gl.bindFramebuffer(gl.FRAMEBUFFER, r.targetFrameBuffer);
      gl.viewport(0, 0, r.texsizeX, r.texsizeY);
      gl.useProgram(program); gl.bindVertexArray(vao); gl.uniform1i(source, 0); gl.uniform1f(amount, opacity);
      gl.enable(gl.BLEND); gl.blendEquation(gl.FUNC_ADD); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    // A feedback hook replaces the retained target before this frame consumes
    // it. The ordinary source is then injected with its independent input mix.
    if (message.feedback_rgba) inject(message.feedback_rgba, 1);
    inject(message.rgba, config.mix);
    gl.bindVertexArray(state.vao); gl.useProgram(state.program);
    gl.bindTexture(gl.TEXTURE_2D, oldTexture); gl.bindSampler(0, oldSampler); gl.activeTexture(state.active);
    gl.bindFramebuffer(gl.FRAMEBUFFER, state.fbo); gl.viewport(...state.viewport);
    gl.blendEquationSeparate(state.eqRGB, state.eqA);
    gl.blendFuncSeparate(state.srcRGB, state.dstRGB, state.srcA, state.dstA);
    if (!state.blend) gl.disable(gl.BLEND);
    viz.render({elapsedTime: 1 / config.fps, audioLevels: {
      timeByteArray: decode(message.mono), timeByteArrayL: decode(message.left), timeByteArrayR: decode(message.right),
    }});
    if (errors.length || gl.isContextLost()) throw new Error(errors.join('\n') || 'WebGL context lost');
    const err = gl.getError(); if (err !== gl.NO_ERROR) throw new Error(`Butterchurn OpenGL error ${err}`);
    if (message.capture === false) return {time:r.time, fps:r.fps};
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const raw = new Uint8Array(config.width * config.height * 4), top = new Uint8Array(raw.length);
    gl.readPixels(0, 0, config.width, config.height, gl.RGBA, gl.UNSIGNED_BYTE, raw);
    const stride = config.width * 4;
    for (let y = 0; y < config.height; y++) top.set(raw.subarray(y*stride, (y+1)*stride), (config.height-y-1)*stride);
    return {rgba: encode(top), time: r.time, fps: r.fps};
  };
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  return {engine: 'butterchurn', version: '2.6.7', opengl: gl.getParameter(gl.VERSION),
    renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    input: 'full composed RGBA injected into feedback before real MilkDrop warp/composite',
    clock: 'pinned calcTimeAndFPS bridge: output frame / fps; fixed fps', seed: config.seed,
    mesh_width: config.mesh_width, mesh_height: config.mesh_height};
}
