"""Native OpenGL 3.3 offscreen mesh and physically based participating-media passes.

ModernGL is optional and only imported for explicit GPU rendering. This is a
real triangle rasteriser (GPU framebuffer, depth texture, shadow-map PCF), not
an image-processing simulation. Mesa/ANGLE may use a software OpenGL driver;
inspect GL_VERSION/GL_RENDERER evidence before assuming hardware acceleration.

Direct GGX lighting and single-scattering ray integration approximate radiative
transfer. Multiple scattering, spectral dispersion and global illumination are
not calculated, and visual results are GPU/driver dependent.
"""

import math

from PIL import Image


VERTEX = """
#version 330
in vec3 in_position;
in vec2 in_uv;
uniform mat4 u_mvp;
uniform mat4 u_model;
uniform mat4 u_light_vp;
out vec3 v_world;
out vec2 v_uv;
out vec4 v_light;
void main() {
    vec4 world = u_model * vec4(in_position, 1.0);
    v_world = world.xyz;
    v_uv = in_uv;
    v_light = u_light_vp * world;
    gl_Position = u_mvp * vec4(in_position, 1.0);
}
"""

SHADOW_VERTEX = """
#version 330
in vec3 in_position;
uniform mat4 u_light_mvp;
void main() {gl_Position = u_light_mvp * vec4(in_position,1.0);}
"""
SHADOW_FRAGMENT = """
#version 330
void main() {}
"""

COMMON = """
uniform int u_octaves;
float hash31(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
}
float noise3(vec3 p) {
    vec3 i=floor(p), f=fract(p);
    f=f*f*(3.0-2.0*f);
    float a=mix(hash31(i),hash31(i+vec3(1,0,0)),f.x);
    float b=mix(hash31(i+vec3(0,1,0)),hash31(i+vec3(1,1,0)),f.x);
    float c=mix(hash31(i+vec3(0,0,1)),hash31(i+vec3(1,0,1)),f.x);
    float d=mix(hash31(i+vec3(0,1,1)),hash31(i+vec3(1,1,1)),f.x);
    return mix(mix(a,b,f.y),mix(c,d,f.y),f.z);
}
float fbm(vec3 p) {
    float s=0.0, a=0.5;
    for(int i=0;i<8;i++){if(i>=u_octaves)break;s+=a*noise3(p);p*=2.01;a*=0.5;}
    return s;
}
float visibility(vec4 lpos, sampler2D shadow) {
    vec3 q=lpos.xyz/max(lpos.w,0.00001)*0.5+0.5;
    if(q.x<=0.0 || q.y<=0.0 || q.x>=1.0 || q.y>=1.0 || q.z>=1.0)
        return 1.0;
    vec2 pixel=1.0/vec2(textureSize(shadow,0));
    float visible=0.0;
    for(int y=-1;y<=1;y++) for(int x=-1;x<=1;x++) {
        float nearest=texture(shadow,q.xy+vec2(x,y)*pixel).r;
        visible+=(q.z-0.002<nearest?1.0:0.0);
    }
    return visible/9.0;
}
"""

MESH_FRAGMENT = """
#version 330
in vec3 v_world;
in vec2 v_uv;
in vec4 v_light;
out vec4 frag;
uniform sampler2D u_shadow;
uniform vec3 u_camera;
uniform vec3 u_light_dir;
uniform vec3 u_light_rgb;
uniform float u_light_power;
uniform float u_roughness;
uniform float u_metallic;
uniform float u_ambient;
uniform float u_texture_scale;
uniform float u_normal_strength;
uniform float u_weathering;
uniform float u_snowline;
uniform float u_glaciation;
uniform float u_ice_coverage;
uniform float u_shadow_strength;
""" + COMMON + """
const float PI=3.141592653589793;
void main(){
    vec3 N=normalize(cross(dFdx(v_world),dFdy(v_world)));
    if(!gl_FrontFacing)N=-N;
    float eps=0.015;
    vec3 p=v_world*u_texture_scale+vec3(v_uv*0.12,0.0);
    float n=fbm(p);
    float dX=(fbm(p+vec3(eps,0,0))-fbm(p-vec3(eps,0,0)))/(2.0*eps);
    float dY=(fbm(p+vec3(0,eps,0))-fbm(p-vec3(0,eps,0)))/(2.0*eps);
    float dZ=(fbm(p+vec3(0,0,eps))-fbm(p-vec3(0,0,eps)))/(2.0*eps);
    N=normalize(N-u_normal_strength*0.1*vec3(dX,dY,dZ));
    vec3 V=normalize(u_camera-v_world),L=normalize(u_light_dir);
    if(dot(N,V)<0.0)N=-N;
    float slope=1.0-abs(N.z);
    float age=clamp(u_weathering*(0.45+0.55*n),0.0,1.0);
    float vegetated=clamp((0.5-slope)*(1.0-age),0.0,1.0);
    vec3 rock=mix(vec3(.17,.145,.13),vec3(.41,.35,.30),n);
    vec3 grass=vec3(.08,.27,.11)+n*vec3(.06,.1,.03);
    vec3 albedo=mix(rock,grass,vegetated);
    float snow=clamp((v_world.z-u_snowline)*1.6+u_glaciation*0.12,0.0,1.0);
    albedo=mix(albedo,vec3(.79,.86,.93),snow);
    float ice=clamp(u_ice_coverage*u_glaciation*max(0.0,(v_world.z-u_snowline+0.15)*2.0),0.0,1.0);
    albedo=mix(albedo,vec3(.34,.68,.78),ice);
    float rough=clamp(u_roughness+u_weathering*(.18*n-.04)-ice*.20,.06,1.0);
    float metal=clamp(u_metallic*(1.0-ice),0.0,1.0);
    vec3 H=normalize(L+V);
    float NoL=max(dot(N,L),0.0),NoV=max(dot(N,V),.0001);
    float NoH=max(dot(N,H),0.0),VoH=max(dot(V,H),0.0);
    float alpha=rough*rough,aa=alpha*alpha;
    float D=aa/(PI*pow(NoH*NoH*(aa-1.0)+1.0,2.0)+.00001);
    float k=pow(rough+1.0,2.0)/8.0;
    float G=NoV/(NoV*(1.0-k)+k)*NoL/(NoL*(1.0-k)+k);
    vec3 F0=mix(vec3(.04),albedo,metal);
    vec3 F=F0+(vec3(1.0)-F0)*pow(1.0-VoH,5.0);
    vec3 spec=D*G*F/max(.0001,4.0*NoV*NoL);
    vec3 diffuse=(vec3(1.0)-F)*(1.0-metal)*albedo/PI;
    float shadow=visibility(v_light,u_shadow);
    shadow=mix(1.0,shadow,u_shadow_strength);
    vec3 radiance=(diffuse+spec)*NoL*shadow*u_light_rgb*u_light_power;
    frag=vec4(albedo*u_ambient*0.25+radiance,1.0);
}
"""

FULL_VERTEX = """
#version 330
out vec2 v_uv;
void main(){
    vec2 pos=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));
    v_uv=pos*0.5;
    gl_Position=vec4(pos-1.0,0.0,1.0);
}
"""

VOLUME_FRAGMENT = """
#version 330
in vec2 v_uv;
out vec4 frag;
uniform sampler2D u_scene;
uniform sampler2D u_depth;
uniform sampler2D u_shadow;
uniform mat4 u_inverse_vp;
uniform mat4 u_light_vp;
uniform vec3 u_camera;
uniform vec3 u_light_dir;
uniform vec3 u_light_rgb;
uniform float u_light_power;
uniform float u_sigma_s;
uniform float u_sigma_a;
uniform float u_anisotropy;
uniform float u_time;
uniform float u_far;
uniform int u_steps;
""" + COMMON + """
const float PI=3.141592653589793;
void main() {
    vec4 source=texture(u_scene,v_uv);
    float z=texture(u_depth,v_uv).r;
    vec4 clip=vec4(v_uv*2.0-1.0,z*2.0-1.0,1.0);
    vec4 world=u_inverse_vp*clip;
    vec3 target=world.xyz/max(world.w,0.00001);
    vec3 ray=normalize(target-u_camera);
    float limit=(z<0.999999)?length(target-u_camera):u_far;
    limit=min(u_far,max(limit,0.0));
    float ds=limit/float(max(1,u_steps));
    float T=1.0;
    vec3 scattered=vec3(0);
    float g=u_anisotropy;
    float cosTheta=dot(ray,-normalize(u_light_dir));
    float phase=(1.0-g*g)/(4.0*PI*pow(1.0+g*g-2.0*g*cosTheta,1.5));
    for(int i=0;i<48;i++) {
        if(i>=u_steps)break;
        vec3 p=u_camera+ray*(float(i)+.5)*ds;
        float variation=0.75+0.45*fbm(p*.36+vec3(0,0,u_time*.04));
        float sigmaS=u_sigma_s*variation;
        float sigmaT=(u_sigma_s+u_sigma_a)*variation;
        float absorb=exp(-sigmaT*ds);
        float shadow=visibility(u_light_vp*vec4(p,1.0),u_shadow);
        vec3 illumination=u_light_rgb*u_light_power*shadow;
        float scatteringWeight=(sigmaT>0.00001)?sigmaS*(1.0-absorb)/sigmaT:0.0;
        scattered+=T*scatteringWeight*phase*illumination;
        T*=absorb;
    }
    vec3 linearColour=source.rgb*T+scattered;
    vec3 encoded=pow(clamp(linearColour/(vec3(1.0)+linearColour),0.0,1.0),vec3(1.0/2.2));
    // Transparent backgrounds stay transparent with zero scattering.
    float alpha=clamp(source.a+(1.0-source.a)*(1.0-T),0.0,1.0);
    frag=vec4(encoded,alpha);
}
"""


def _look_at(np, eye, center, up):
    e=np.asarray(eye,dtype=np.float64)
    forward=np.asarray(center,dtype=np.float64)-e
    forward/=np.linalg.norm(forward)
    up=np.asarray(up,dtype=np.float64)
    if abs(float(np.dot(forward,up)))>0.98:
        up=np.asarray((0.0,0.0,1.0),dtype=np.float64)
    right=np.cross(forward,up)
    right/=np.linalg.norm(right)
    upward=np.cross(right,forward)
    out=np.eye(4,dtype=np.float64)
    out[0,:3]=right
    out[1,:3]=upward
    out[2,:3]=-forward
    out[:3,3]=-out[:3,:3]@e
    return out


def _perspective(np, fov, aspect, near=.1, far=60):
    f=1/math.tan(math.radians(fov)/2)
    m=np.zeros((4,4),dtype=np.float64)
    m[0,0]=f/aspect
    m[1,1]=f
    m[2,2]=-(far+near)/(far-near)
    m[2,3]=-2*far*near/(far-near)
    m[3,2]=-1
    return m


def _orthographic(np, halfwidth=5, near=.1, far=30):
    m=np.eye(4,dtype=np.float64)
    m[0,0]=m[1,1]=1/halfwidth
    m[2,2]=-2/(far-near)
    m[2,3]=-(far+near)/(far-near)
    return m


def _model(np, yaw, pitch):
    a,b=math.radians(yaw),pitch
    ca,sa,cb,sb=math.cos(a),math.sin(a),math.cos(b),math.sin(b)
    y=np.array([[ca,0,sa,0],[0,1,0,0],[-sa,0,ca,0],[0,0,0,1]],dtype=np.float64)
    x=np.array([[1,0,0,0],[0,cb,-sb,0],[0,sb,cb,0],[0,0,0,1]],dtype=np.float64)
    return x@y


class GLMeshRenderer:
    def __init__(self, size, shadow_map_size):
        try:
            import moderngl
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install framerudiments[gpu] (ModernGL + NumPy) for native mesh rendering") from exc
        self.gl,self.np=moderngl,np
        try:
            try:
                self.ctx=moderngl.create_standalone_context(require=330,backend="egl")
            except Exception:
                self.ctx=moderngl.create_standalone_context(require=330)
            self.surface=self.ctx.program(vertex_shader=VERTEX,fragment_shader=MESH_FRAGMENT)
            self.depth_shader=self.ctx.program(vertex_shader=SHADOW_VERTEX,fragment_shader=SHADOW_FRAGMENT)
            self.volume=self.ctx.program(vertex_shader=FULL_VERTEX,fragment_shader=VOLUME_FRAGMENT)
            self.shadow_texture=self.ctx.depth_texture((shadow_map_size,shadow_map_size))
            self.shadow_texture.filter=(moderngl.NEAREST,moderngl.NEAREST)
            self.shadow_texture.repeat_x=self.shadow_texture.repeat_y=False
            self.shadow_texture.compare_func=""
            self.shadow_frame=self.ctx.framebuffer(depth_attachment=self.shadow_texture)
            self.scene_color=self.ctx.texture(size,components=4,dtype="f2")
            self.scene_depth=self.ctx.depth_texture(size)
            self.scene_depth.compare_func=""
            self.scene_frame=self.ctx.framebuffer(color_attachments=[self.scene_color],
                                                   depth_attachment=self.scene_depth)
            self.final_color=self.ctx.texture(size,components=4,dtype="f1")
            self.final_frame=self.ctx.framebuffer(color_attachments=[self.final_color])
            self.full_vao=self.ctx.vertex_array(self.volume,[])
            self.renderer_info=str(self.ctx.info.get("GL_RENDERER","unknown"))
            self.version_info=str(self.ctx.info.get("GL_VERSION","unknown"))
        except Exception as exc:
            self.close()
            raise RuntimeError("OpenGL 3.3 scene or shader initialization failed") from exc

    def _mat(self, program, name, matrix):
        program[name].write(matrix.astype("f4").T.tobytes())

    def render(self, scene, mesh, frame):
        np,gl=self.np,self.gl
        values=tuple(frame.values)
        t=float(frame.context.output_seconds)
        angle=scene.base.rotation+scene.base.orbit*t+values[0]*45
        pitch=.32+(values[1] if len(values)>1 else values[0])*.38+math.radians(scene.camera_pitch)
        model=_model(np,angle,pitch)
        distance=scene.base.distance
        camera=np.asarray((0.,0.,distance),dtype="f4")
        view=_look_at(np,camera,(0,0,0),(0,1,0))
        projection=_perspective(np,scene.base.fov,scene.size[0]/scene.size[1])
        vp=projection@view
        light=np.asarray(scene.base.light,dtype="f4")
        lview=_look_at(np,light*9,(0,0,0),(0,1,0))
        lvp=_orthographic(np)@lview
        vertices=[]
        for triangle in mesh.triangles:
            for idx in triangle:
                vertices.extend(mesh.positions[idx])
        vbo=self.ctx.buffer(np.asarray(vertices,dtype="f4").tobytes())
        try:
            vao_depth=self.ctx.vertex_array(self.depth_shader,[(vbo,"3f 8x","in_position")])
            vao_scene=self.ctx.vertex_array(self.surface,[(vbo,"3f 2f","in_position","in_uv")])
            try:
                self.ctx.enable(gl.DEPTH_TEST)
                self.ctx.disable(gl.BLEND)
                self.shadow_frame.use()
                self.ctx.viewport=(0,0,*self.shadow_texture.size)
                self.shadow_frame.clear(depth=1.0)
                self._mat(self.depth_shader,"u_light_mvp",lvp@model)
                vao_depth.render(mode=gl.TRIANGLES)

                self.scene_frame.use()
                self.ctx.viewport=(0,0,*scene.size)
                rgba=scene.background
                def linear(x):
                    v=x/255
                    return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
                self.scene_frame.clear(*[linear(v) for v in rgba[:3]],rgba[3]/255,depth=1.0)
                self._mat(self.surface,"u_mvp",vp@model)
                self._mat(self.surface,"u_model",model)
                self._mat(self.surface,"u_light_vp",lvp)
                for name,value in {
                    "u_camera":tuple(camera),"u_light_dir":tuple(light),
                    "u_light_rgb":tuple(v/255 for v in scene.base.light_colour),
                    "u_light_power":scene.light_power,"u_roughness":scene.base.roughness,
                    "u_metallic":scene.base.metallic,"u_ambient":scene.base.ambient,
                    "u_texture_scale":scene.texture_scale,"u_normal_strength":scene.normal_strength,
                    "u_weathering":scene.weathering,"u_snowline":scene.snowline,
                    "u_glaciation":scene.glaciation,"u_ice_coverage":scene.ice_coverage,
                    "u_shadow_strength":scene.shadow_strength,"u_octaves":scene.texture_octaves,
                    "u_shadow":0,
                }.items():
                    self.surface[name].value=value
                self.shadow_texture.use(location=0)
                vao_scene.render(mode=gl.TRIANGLES)

                self.final_frame.use()
                self.ctx.disable(gl.DEPTH_TEST)
                self.ctx.viewport=(0,0,*scene.size)
                self.scene_color.use(location=0)
                self.scene_depth.use(location=1)
                self.shadow_texture.use(location=2)
                self._mat(self.volume,"u_inverse_vp",np.linalg.inv(vp))
                self._mat(self.volume,"u_light_vp",lvp)
                for name,value in {
                    "u_camera":tuple(camera), "u_light_dir":tuple(light),
                    "u_light_rgb":tuple(v/255 for v in scene.base.light_colour),
                    "u_light_power":scene.light_power,
                    "u_sigma_s":scene.scattering_density*scene.base.fog,
                    "u_sigma_a":scene.absorption_density*scene.base.fog,
                    "u_anisotropy":scene.anisotropy,"u_time":t,
                    "u_steps":scene.volumetric_steps,"u_octaves":scene.texture_octaves,
                    "u_far":min(16, distance*2.0),
                    "u_scene":0,"u_depth":1,"u_shadow":2,
                }.items():
                    self.volume[name].value=value
                self.full_vao.render(mode=gl.TRIANGLES,vertices=3)
                data=self.final_frame.read(components=4,alignment=1)
                out=Image.frombytes("RGBA",scene.size,data).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                return out,len(mesh.triangles)
            finally:
                vao_depth.release()
                vao_scene.release()
        finally:
            vbo.release()

    def close(self):
        for name in ("full_vao","shadow_frame","scene_frame","final_frame",
                     "shadow_texture","scene_color","scene_depth","final_color",
                     "surface","depth_shader","volume","ctx"):
            obj=getattr(self,name,None)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
