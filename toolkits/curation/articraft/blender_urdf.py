"""解析自包含 URDF(primitive),FK归零位摆放,Blender GPU 渲染多视角,透明背景。
渲两套: view_XX.png(URDF材质彩色) + clay_XX.png(白模)。
用法: blender --background --python blender_urdf.py -- <urdf> <out_dir> [res] [samples] [n_azim]"""
import bpy, sys, os, math, random
import xml.etree.ElementTree as ET
from mathutils import Matrix, Vector

argv=sys.argv[sys.argv.index("--")+1:]
URDF, OUT = argv[0], argv[1]
RES=int(argv[2]) if len(argv)>2 else 1024
SAMPLES=int(argv[3]) if len(argv)>3 else 48
NAZ=int(argv[4]) if len(argv)>4 else 8
MODE=argv[5] if len(argv)>5 else "material"   # material | clay | both
os.makedirs(OUT,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene

def rpy_m(r,p,y): return Matrix.Rotation(y,4,'Z')@Matrix.Rotation(p,4,'Y')@Matrix.Rotation(r,4,'X')
def origin_m(o):
    if o is None: return Matrix.Identity(4)
    xyz=[float(x) for x in o.get('xyz','0 0 0').split()]
    rpy=[float(x) for x in o.get('rpy','0 0 0').split()]
    return Matrix.Translation(Vector(xyz))@rpy_m(*rpy)

root=ET.parse(URDF).getroot()
# 顶层 material 定义 name->rgba
TOP={}
for m in root.findall('material'):
    c=m.find('color')
    if c is not None and c.get('rgba'): TOP[m.get('name')]=[float(x) for x in c.get('rgba').split()]

def visual_color(v):
    mat=v.find('material')
    if mat is None: return None
    c=mat.find('color')
    if c is not None and c.get('rgba'): return [float(x) for x in c.get('rgba').split()]
    return TOP.get(mat.get('name'))

links={}
for link in root.findall('link'):
    vs=[]
    for v in link.findall('visual'):
        g=v.find('geometry')
        if g is None or len(g)==0: continue
        vs.append((g[0], v.find('origin'), visual_color(v)))
    links[link.get('name')]=vs
joints=[]; children=set()
for j in root.findall('joint'):
    p=j.find('parent').get('link'); c=j.find('child').get('link')
    joints.append((p,c,j.find('origin'))); children.add(c)
roots=[n for n in links if n not in children]
world={}
def dfs(n,T):
    world[n]=T
    for p,c,o in joints:
        if p==n and c not in world: dfs(c,T@origin_m(o))
for r in roots: dfs(r,Matrix.Identity(4))
for n in links: world.setdefault(n,Matrix.Identity(4))

def mk_mat(rgba):
    m=bpy.data.materials.new("m"); m.use_nodes=True
    b=m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value=tuple(rgba)
    b.inputs["Roughness"].default_value=0.5
    return m
matcache={}
def color_mat(color):
    key=tuple(color) if color else (0.8,0.8,0.82,1.0)
    if len(key)==3: key=key+(1.0,)
    if key not in matcache: matcache[key]=mk_mat(key)
    return matcache[key]
CLAY=mk_mat((0.82,0.82,0.84,1.0))

objs=[]   # (obj, color_material)
for ln,vs in links.items():
    LW=world[ln]
    for geo,o,color in vs:
        VW=LW@origin_m(o); t=geo.tag
        if t=='box':
            s=[float(x) for x in geo.get('size').split()]
            bpy.ops.mesh.primitive_cube_add(size=1); ob=bpy.context.active_object
            ob.matrix_world=VW@Matrix.Diagonal((s[0],s[1],s[2],1))
        elif t=='cylinder':
            r=float(geo.get('radius')); l=float(geo.get('length'))
            bpy.ops.mesh.primitive_cylinder_add(radius=1,depth=1,vertices=48); ob=bpy.context.active_object
            ob.matrix_world=VW@Matrix.Diagonal((r,r,l,1))
        elif t=='sphere':
            r=float(geo.get('radius'))
            bpy.ops.mesh.primitive_uv_sphere_add(radius=1,segments=48,ring_count=24); ob=bpy.context.active_object
            ob.matrix_world=VW@Matrix.Diagonal((r,r,r,1))
        else: continue
        cm=color_mat(color); ob.data.materials.append(cm)
        objs.append((ob,cm))

mn=Vector((1e18,)*3); mx=Vector((-1e18,)*3)
for ob,_ in objs:
    for c in ob.bound_box:
        w=ob.matrix_world@Vector(c)
        for k in range(3): mn[k]=min(mn[k],w[k]); mx[k]=max(mx[k],w[k])
center=(mn+mx)/2; radius=max((mx-mn).length/2, 1e-4)

# 破除"精确共面/重合面"导致的黑块: 给每个 visual 加微小确定性位移抖动。
# URDF 由大量 box 堆叠拼装, 相邻部件常共享完全重合的面; Cycles 下深度竞争中
# "胜出"的面会落在相邻实体内部而被自遮挡 → 渲成纯黑方块。亚毫米级抖动让重合面
# 明确分到"内部(被正常遮挡隐藏)"或"外部(正常受光)", 实测可消除 ~90% 黑块。
# 幅度按模型尺度自适应(对小物体按比例更小), 视觉上不可见。固定 seed 保证可复现。
random.seed(12345)
JIT=radius*0.002
for ob,_ in objs:
    ob.matrix_world=Matrix.Translation(Vector((random.uniform(-JIT,JIT) for _ in range(3))))@ob.matrix_world

w=bpy.data.worlds.new("w"); scene.world=w; w.use_nodes=True
bg=w.node_tree.nodes["Background"]; bg.inputs[0].default_value=(1,1,1,1); bg.inputs[1].default_value=0.55
sd=bpy.data.lights.new("s",type='SUN'); sd.energy=3.0; sd.angle=math.radians(8)
so=bpy.data.objects.new("s",sd); scene.collection.objects.link(so)
so.rotation_euler=(math.radians(52),math.radians(12),math.radians(-50))
FOV=math.radians(45)
cd=bpy.data.cameras.new("c"); cd.sensor_fit='AUTO'; cd.angle=FOV
co=bpy.data.objects.new("c",cd); scene.collection.objects.link(co); scene.camera=co

scene.render.engine='CYCLES'
prefs=bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type='CUDA'; prefs.refresh_devices()
gpu=any(d.type=='CUDA' for d in prefs.devices)
for d in prefs.devices: d.use=(d.type=='CUDA')
scene.cycles.device='GPU' if gpu else 'CPU'
scene.cycles.samples=SAMPLES; scene.cycles.use_denoising=True
try:
    scene.cycles.denoiser='OPENIMAGEDENOISE'; scene.cycles.denoising_use_gpu=True  # GPU降噪,省CPU
except Exception: pass
scene.render.resolution_x=RES; scene.render.resolution_y=RES
scene.render.film_transparent=True
scene.render.image_settings.file_format='PNG'; scene.render.image_settings.color_mode='RGBA'

dist=radius/math.sin(FOV/2)*1.15   # bounding-sphere fit, 完整入画
elev=math.radians(25)
poses=[]
for i in range(NAZ):
    a=math.radians(i*360.0/NAZ)
    eye=center+Vector((dist*math.cos(elev)*math.cos(a),dist*math.cos(elev)*math.sin(a),dist*math.sin(elev)))
    poses.append(eye)

def render_set(prefix):
    for i,eye in enumerate(poses):
        co.location=eye; co.rotation_euler=(center-eye).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=os.path.join(OUT,f"{prefix}_{i:02d}.png")
        bpy.ops.render.render(write_still=True)

if MODE=="clay":
    for ob,_ in objs: ob.data.materials.clear(); ob.data.materials.append(CLAY)
    render_set("view")               # 白模, 命名 view_XX
elif MODE=="both":
    render_set("view")               # 彩色
    for ob,_ in objs: ob.data.materials.clear(); ob.data.materials.append(CLAY)
    render_set("clay")               # 白模
else:  # material
    render_set("view")               # 彩色(URDF材质), 命名 view_XX
print(f"URDF_RENDER_DONE objs={len(objs)} views={NAZ} mode={MODE} colored_mats={len(matcache)}")
