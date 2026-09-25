"""Executable numerical contracts for the recipes agents receive."""
from __future__ import annotations

import math
import re

import numpy as np
import pytest
from PIL import Image

from codeverse3d.languages.glsl_shader import compose
from codeverse3d.prompts import load_text
from codeverse3d.spatial.gl_render import GlHost, GlHostError
from codeverse3d.tracks.graphics import cookbook_functions, with_helpers

pytest.importorskip("moderngl")


def test_sdf_transport_and_surface_response_on_real_gl(tmp_path):
    known = cookbook_functions(load_text("glsl_shader/cookbook.md"))
    requested = ("sdStar", "sdSegment", "sun", "dropsLayer", "bokehSoft", "beerTransmittance", "phaseHG", "pbrDirect", "aurora")
    recipes = "\n\n".join(r.text for r in with_helpers([known[k] for k in requested], known))
    shader = """
void mainImage(out vec4 fragColor,in vec2 fragCoord) {
 float failures=0.0;
 float segment=sdSegment(vec2(.3,.4),vec2(0),vec2(0));
 if(isnan(segment)||abs(segment-.5)>1e-5) failures+=1.0;
 float halfSector=3.14159265/5.0;
 if(sdStar(.6*vec2(cos(halfSector),sin(halfSector)),1.0,5.0)<=0.0) failures+=1.0;
 if(abs(sdStar(vec2(0),1.0,5.0)+.45)>1e-5) failures+=1.0;
 if(abs(sdStar(vec2(2,0),1.0,5.0)-1.0)>1e-5) failures+=1.0;
 float a=beerTransmittance(.7,2.0),b=beerTransmittance(.7,3.0);
 if(abs(a*b-beerTransmittance(.7,5.0))>1e-6) failures+=1.0;
 if(beerTransmittance(.7,0.0)!=1.0) failures+=1.0;
 float phaseIntegral=0.0;
 for(int i=0;i<2048;i++) phaseIntegral+=phaseHG(-1.0+2.0*(float(i)+.5)/2048.0,.8)*12.56637061/2048.0;
 if(abs(phaseIntegral-1.0)>.002) failures+=1.0;
 if(phaseHG(1.0,.8)<=phaseHG(-1.0,.8)) failures+=1.0;
 vec3 n=vec3(0,0,1),v=normalize(vec3(.3,0,1)),l=normalize(vec3(-.2,.4,1));
 vec3 forward=pbrDirect(vec3(.7,.4,.2),.4,.35,n,v,l,vec3(1))/dot(n,l);
 vec3 reverse=pbrDirect(vec3(.7,.4,.2),.4,.35,n,l,v,vec3(1))/dot(n,v);
 if(length(forward-reverse)>1e-5) failures+=1.0;
 if(length(pbrDirect(vec3(1),0.0,.3,n,v,-n,vec3(1)))>0.0) failures+=1.0;
 vec3 integrated=vec3(0);
 for(int i=0;i<2048;i++) {
  float z=(float(i)+.5)/2048.0,angle=float(i)*2.39996323;
  vec3 light=vec3(sqrt(1.0-z*z)*vec2(cos(angle),sin(angle)),z);
  integrated+=pbrDirect(vec3(1),0.0,.5,n,n,light,vec3(1))*6.28318531/2048.0;
 }
 if(any(greaterThan(integrated,vec3(1.01)))||any(lessThan(integrated,vec3(.8)))) failures+=1.0;
 vec2 uv=fragCoord/u_resolution;
 vec2 rain=dropsLayer(uv,u_time,8.0);
 vec3 bokeh=bokehSoft(uv,u_time);
 float disc=sun(uv,vec2(.5),.1);
 if(any(isnan(rain))||any(isnan(bokeh))||isnan(disc)) failures+=1.0;
 if(any(lessThan(rain,vec2(0)))||any(greaterThan(rain,vec2(1)))) failures+=1.0;
 // The color of each curtain uses its OWN output height. Evaluating color
 // before an out-parameter call silently borrowed the preceding layer's k.
 vec2 p=uv-vec2(.5,.15); float k=0.0; vec3 expectedAurora=vec3(0);
 float a0=curtain(p,u_time,0.0,k); expectedAurora+=auroraCol(k)*a0;
 float a1=curtain(p-vec2(.3,.12),u_time*.8,11.0,k); expectedAurora+=auroraCol(k)*a1*.55;
 float a2=curtain(p-vec2(-.5,.22),u_time*.6,23.0,k); expectedAurora+=auroraCol(k)*a2*.30;
 if(length(aurora(p,u_time)-expectedAurora)>1e-6) failures+=1.0;
 fragColor=vec4(min(failures,1.0),failures==0.0?1.0:0.0,failures/16.0,1.0);
}
"""
    try:
        result = GlHost(timeout_s=90).render_fragment_shader(
            compose(shader, recipes_src=recipes).source, tmp_path / "contracts",
            width=16, height=16, times=(0.0, 1.7))
    except GlHostError as exc:
        pytest.skip(f"OpenGL unavailable: {exc}")
    assert result.ok, result.error_message
    for frame in result.frames:
        assert frame.nan == frame.inf == 0
        pixels = np.asarray(Image.open(frame.path).convert("RGB"))
        assert np.all(pixels == [0, 255, 0]), pixels.max(axis=(0, 1)).tolist()


def test_opengl_cookbook_sphere_winding_matches_its_outward_normals():
    text = load_text("opengl_python/cookbook.md")
    code = next(block for block in re.findall(r"```python\n(.*?)```", text, re.S)
                if "def sphere_mesh" in block)
    namespace = {"np": np, "math": math}
    exec(code, namespace)
    raw, indices = namespace["sphere_mesh"](12, 24, 2.0)
    vertices = raw.reshape(-1, 6)
    triangles = vertices[indices.reshape(-1, 3)]
    geometric = np.cross(triangles[:, 1, :3] - triangles[:, 0, :3],
                         triangles[:, 2, :3] - triangles[:, 0, :3])
    nondegenerate = np.linalg.norm(geometric, axis=1) > 1e-6
    alignment = np.sum(geometric * triangles[:, :, 3:].mean(axis=1), axis=1)
    assert np.all(alignment[nondegenerate] > 0)
