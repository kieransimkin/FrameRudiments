"""Original GPU volume flame: 3-D advected density and emission/absorption rays."""

from PIL import Image

from .io import integer, number

VERTEX = """#version 330
in vec2 in_pos;out vec2 uv;
void main(){uv=in_pos*.5+.5;gl_Position=vec4(in_pos,0,1);}
"""
FRAGMENT = """#version 330
in vec2 uv;out vec4 colour;
uniform float seconds;uniform float seed;uniform float aspect;
uniform int steps;
float hash(vec3 p){return fract(sin(dot(p,vec3(127.1,311.7,74.7))+seed*13.71)*43758.5453);}
float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
float fbm(vec3 p){float sum=0.,a=.53;for(int i=0;i<5;i++){sum+=noise(p)*a;p=p*2.03+vec3(7.1,13.9,2.3);a*=.5;}return sum;}
vec3 emission(float heat){float K=650.+1900.*clamp(heat,0.,1.);vec3 L=vec3(700e-9,546e-9,436e-9);
vec3 B=pow(vec3(700e-9)/L,vec3(5))*(exp(.01438776877/(700e-9*2450.))-1.)/(exp(vec3(.01438776877)/(L*K))-1.);return B;}
void main(){
 vec2 p=vec2((uv.x-.5)*aspect*2.5,uv.y*2.8);
 float T=1.;vec3 light=vec3(0);float ds=1.6/float(steps);
 for(int k=0;k<96;k++){if(k>=steps)break;float z=-.8+(float(k)+.5)*ds;
  vec3 q=vec3(p,z);vec3 flow=q*2.7-vec3(0,seconds*2.4,0);
  float warp=fbm(flow*.65+vec3(3.1,0,7.2))-.5;
  float n=fbm(flow+vec3(warp*1.7,warp*.5,warp));
  float lateral=exp(-pow(abs(q.x)/max(.1,aspect*1.13),8.));
  float depth=exp(-pow(abs(z)*1.8,4.));
  float envelope=(1.-smoothstep(.1,2.6,p.y))*lateral*depth;
  float front=.5+p.y*.075;
  float sheet=exp(-pow((n-front)/.065,2.));
  float density=sheet*envelope*(1.-smoothstep(1.6,2.6,p.y));
  float absorb=exp(-density*ds*1.6);
  float heat=clamp(.35+density*.4+(1.-p.y/2.8)*.30,0.,1.);
  light+=T*(1.-absorb)*emission(heat)*1.25;
  T*=absorb;if(T<.004)break;
 }
 vec3 mapped=pow(vec3(1)-exp(-light*1.6),vec3(1./2.2));
 float alpha=clamp(1.-T,0.,1.);
 colour=vec4(clamp(mapped/max(alpha,.001),0.,1.),alpha);
}
"""


class VolumeFire:
    def __init__(self, size, *, seed=7, steps=64):
        try:
            import moderngl
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Volume fire requires framerudiments[gpu]") from exc
        self.gl = moderngl
        self.size = size
        self.seed = integer(seed, "volume seed", maximum=65535)
        self.steps = integer(steps, "volume ray steps", minimum=16, maximum=96)
        self.resources = []
        try:
            try:
                self.ctx = moderngl.create_standalone_context(require=330, backend="egl")
            except Exception:
                self.ctx = moderngl.create_standalone_context(require=330)
            self.resources.append(self.ctx)
            self.program = self.ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
            self.resources.append(self.program)
            self.vbo = self.ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
            self.resources.append(self.vbo)
            self.vao = self.ctx.simple_vertex_array(self.program, self.vbo, "in_pos")
            self.resources.append(self.vao)
            self.texture = self.ctx.texture(size, 4, dtype="f1")
            self.resources.append(self.texture)
            self.target = self.ctx.framebuffer(color_attachments=[self.texture])
            self.resources.append(self.target)
            self.evidence = {
                "method": "volume",
                "context_version": self.ctx.info.get("GL_VERSION"),
                "renderer": self.ctx.info.get("GL_RENDERER"),
                "steps": self.steps,
                "seed": self.seed,
                "shader": "original3D density / emission-absorption ray integration",
                "limits": "Procedural volume, not a fluid or chemical simulation; three-wavelength thermal colour approximation; GPU pixels may vary",
            }
        except BaseException:
            self.close()
            raise

    def frame(self, seconds):
        t = number(seconds, "volume source seconds", minimum=0, maximum=86400)
        self.target.use()
        self.ctx.viewport = (0, 0, *self.size)
        self.target.clear(0, 0, 0, 0)
        for key, value in {
            "seconds": t,
            "seed": float(self.seed),
            "aspect": self.size[0] / self.size[1],
            "steps": self.steps,
        }.items():
            self.program[key].value = value
        self.vao.render(mode=self.gl.TRIANGLE_STRIP, vertices=4)
        data = self.target.read(components=4, alignment=1)
        return Image.frombytes("RGBA", self.size, data).transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    def close(self):
        for resource in reversed(getattr(self, "resources", [])):
            resource.release()
        self.resources = []
        if hasattr(self, "evidence"):
            self.evidence["cleanup_verified"] = True
