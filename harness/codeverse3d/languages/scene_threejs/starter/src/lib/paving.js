/** Foreground paving with closed stones, rounded cut edges and recessed joints.
 * Units are metres. The nominal walking surface is local y=0, the underside
 * is -thickness; relief varies the stones around that nominal surface.
 * sampleHeight(x,z) follows the actual Float32 triangles and the joint bed,
 * returning null outside the rectangular patch. It uses LOCAL coordinates.
 * Two merged meshes keep draw cost independent of the number of stones.
 */
import * as THREE from 'three';
import { mulberry32, fbm2 } from './noise.js';
import { patchStandard } from './shader.js';
import { attachDisposal, snapshotResources } from './lifecycle.js';

const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const lerp=(a,b,t)=>a+(b-a)*t;
const mixPoint=(a,b,t)=>[lerp(a[0],b[0],t),lerp(a[1],b[1],t)];

function clip(poly,nx,nz,limit) {
  const out=[];
  for(let i=0;i<poly.length;i++) {
    const a=poly[i],b=poly[(i+1)%poly.length];
    const da=a[0]*nx+a[1]*nz-limit,db=b[0]*nx+b[1]*nz-limit;
    if(da<=1e-12)out.push(a);
    if(da*db<0)out.push(mixPoint(a,b,da/(da-db)));
  }
  return out.filter((p,i)=>Math.hypot(p[0]-out[(i+out.length-1)%out.length][0],
    p[1]-out[(i+out.length-1)%out.length][1])>1e-9);
}

function inset(poly,distance) {
  let out=poly;
  for(let i=0;i<poly.length&&out.length>2;i++) {
    const a=poly[i],b=poly[(i+1)%poly.length];
    const length=Math.hypot(b[0]-a[0],b[1]-a[1]);
    const nx=(b[1]-a[1])/length,nz=-(b[0]-a[0])/length;
    out=clip(out,nx,nz,a[0]*nx+a[1]*nz-distance);
  }
  return out;
}

function cells(width,depth,stoneSize,pattern,rand) {
  // Cap the grid before allocating. Extreme aspect ratios still retain one
  // row, so the bound remains meaningful for long, narrow paths.
  let nx=Math.max(1,Math.ceil(width/stoneSize));
  let nz=Math.max(1,Math.ceil(depth/stoneSize));
  const budget=pattern==='setts'?2048:4096;
  const factor=Math.sqrt(nx*nz/budget);
  if(factor>1){nx=Math.max(1,Math.floor(nx/factor));nz=Math.max(1,Math.floor(nz/factor));}
  if(nx*nz>budget){if(nx>nz)nx=Math.floor(budget/nz);else nz=Math.floor(budget/nx);}
  const sx=width/nx,sz=depth/nz,result=[];
  if(pattern==='setts') {
    for(let row=0;row<nz;row++) {
      const shift=(row%2)*sx*.5,bounds=[-width/2];
      for(let col=1;col<=nx;col++) {
        const x=-width/2+col*sx-shift+(rand()-.5)*sx*.14;
        if(x>-width/2+sx*.12&&x<width/2-sx*.12)bounds.push(x);
      }
      bounds.push(width/2);
      for(let col=0;col<bounds.length-1;col++)result.push([
        [bounds[col],-depth/2+row*sz],[bounds[col+1],-depth/2+row*sz],
        [bounds[col+1],-depth/2+(row+1)*sz],[bounds[col],-depth/2+(row+1)*sz],
      ]);
    }
  } else {
    const sites=[];
    for(let z=0;z<nz;z++)for(let x=0;x<nx;x++)sites.push([
      -width/2+(x+.5+(rand()-.5)*.78)*sx,
      -depth/2+(z+.5+(rand()-.5)*.78)*sz,
    ]);
    // Neighbor bounds are derived from physical distances. A fixed +/-2
    // index window would miss neighbors when the last row is very narrow.
    const rx=Math.ceil(3*Math.max(sx,sz)/sx),rz=Math.ceil(3*Math.max(sx,sz)/sz);
    for(let z=0;z<nz;z++)for(let x=0;x<nx;x++) {
      const a=sites[z*nx+x];
      let poly=[[-width/2,-depth/2],[width/2,-depth/2],[width/2,depth/2],[-width/2,depth/2]];
      for(let j=Math.max(0,z-rz);j<=Math.min(nz-1,z+rz)&&poly.length>2;j++)
      for(let i=Math.max(0,x-rx);i<=Math.min(nx-1,x+rx)&&poly.length>2;i++) {
        if(i===x&&j===z)continue;
        const b=sites[j*nx+i],dx=b[0]-a[0],dz=b[1]-a[1];
        poly=clip(poly,dx,dz,(b[0]*b[0]+b[1]*b[1]-a[0]*a[0]-a[1]*a[1])/2);
      }
      result.push(poly);
    }
  }
  return {polygons:result,spacing:Math.min(sx,sz)};
}

function surfaceMaterial(opts,bedHeight) {
  const moisture=clamp(opts.moisture??0,0,1);
  const material=new THREE.MeshStandardMaterial({color:opts.color??0x626158,flatShading:true,
    vertexColors:true,roughness:clamp(opts.roughness??.86,.05,1),metalness:0});
  material.name='CutPavingStone';
  patchStandard(material,{
    name:'paving:stone',uniforms:{uPaveMoisture:{value:moisture},uPaveBed:{value:bedHeight}},
    vertexHead:'varying vec3 vPaveLocal;',vertexBody:'vPaveLocal=position;',
    fragmentHead:`
      varying vec3 vPaveLocal;
      uniform float uPaveMoisture, uPaveBed;
    `,
    fragmentBody:`
      vec2 paveP=vPaveLocal.xz+vPaveLocal.y*vec2(.47,.81);
      float paveFoot=max(length(dFdx(paveP)),length(dFdy(paveP)));
      float paveFineFade=1.0-smoothstep(.001,.006,paveFoot);
      float paveGrainFade=1.0-smoothstep(.004,.02,paveFoot);
      float paveGrain=(astraNoise2(paveP*63.0)-.5)*paveGrainFade;
      float paveFine=(astraNoise2(paveP*310.0)-.5)*paveFineFade;
      float paveMacro=astraFbm2(paveP*5.0,3)-.4375;
      float paveContact=1.0-smoothstep(uPaveBed-.002,uPaveBed+.02,vPaveLocal.y);
      diffuseColor.rgb*=1.0+paveMacro*.22+paveGrain*.13+paveFine*.09;
      diffuseColor.rgb*=mix(1.0,.63,uPaveMoisture)*mix(1.0,.78,paveContact);
    `,
    roughnessBody:`
      roughnessFactor=clamp(mix(roughnessFactor+paveGrain*.10,.30,uPaveMoisture)
        +paveContact*.09,.08,1.0);
    `,
    normalBody:'normal=astraBump(-vViewPosition,normal,paveGrain*.0009+paveFine*.00025);',
  });
  return material;
}

/** Build cobbles or staggered rectangular setts. No external assets needed.
 * stoneSize is the approximate cell width; at most 4096 stones are generated,
 * so exceptionally large patches automatically use coarser stones.
 * joint, thickness and relief are metres. Zero relief removes height/tilt
 * variation but keeps the rounded cut edges and small intrinsic crown.
 * Materials remain ordinary physical dielectric surfaces with real shadows.
 */
export function makePaving(opts={}) {
  const size=opts.size??[4,4],width=Array.isArray(size)?size[0]:size,
    depth=Array.isArray(size)?size[1]:size;
  const stoneSize=opts.stoneSize??.24,thickness=opts.thickness??.10;
  const pattern=opts.pattern??'cobble';
  for(const [name,value] of Object.entries({width,depth,stoneSize,thickness,
    joint:opts.joint??.012,relief:opts.relief??.014,seed:opts.seed??17,
    moisture:opts.moisture??0,roughness:opts.roughness??.86})) {
    if(!Number.isFinite(value)||(['width','depth','stoneSize','thickness'].includes(name)&&value<=0))
      throw new RangeError(`${name} must be finite${['width','depth','stoneSize','thickness'].includes(name)?' and positive':''}`);
  }
  if(!['cobble','setts'].includes(pattern))throw new RangeError('pattern must be cobble or setts');
  if((opts.joint??.012)<0||(opts.relief??.014)<0)throw new RangeError('joint and relief must be nonnegative');
  const seed=opts.seed??17,rand=mulberry32(seed),layout=cells(width,depth,stoneSize,pattern,rand);
  const joint=clamp(opts.joint??.012,0,layout.spacing*.22);
  const relief=clamp(opts.relief??.014,0,thickness*.35);
  const jointDepth=Math.min(thickness*.62,Math.max(layout.spacing*.085,.006));
  const bedHeight=-jointDepth;
  const positions=[],colors=[],indices=[],ranges=[],outlines=[];
  const color=new THREE.Color();
  const triangle=(a,b,c)=>indices.push(a,c,b); // XZ counterclockwise points face +Y.
  const vertex=(p,c)=>{const i=positions.length/3;positions.push(...p);colors.push(c.r,c.g,c.b);return i;};
  for(const cell of layout.polygons) {
    let poly=inset(cell,joint*.5);
    if(poly.length<3)continue;
    const center=poly.reduce((sum,p)=>[sum[0]+p[0]/poly.length,sum[1]+p[1]/poly.length],[0,0]);
    // Trim each convex corner. Every point stays inside its own cell and
    // contributes to a real curved/chipped bevel rather than a bump outline.
    const rounded=[];
    for(let i=0;i<poly.length;i++) {
      const previous=poly[(i+poly.length-1)%poly.length],p=poly[i],next=poly[(i+1)%poly.length];
      const trim=(pattern==='cobble'?.22:.10)*(0.7+rand()*.6);
      rounded.push(mixPoint(p,previous,trim),mixPoint(p,next,trim));
    }
    poly=rounded;
    const radius=Math.min(...poly.map(p=>Math.hypot(p[0]-center[0],p[1]-center[1])));
    if(radius<Math.max(1e-6,joint*.15))continue;
    const height=(rand()-.5)*relief,tx=(rand()-.5)*relief*.35/radius,tz=(rand()-.5)*relief*.35/radius;
    const crown=Math.min(radius*.019,thickness*.022)*(pattern==='cobble'?1:.24);
    const bevel=Math.min(radius*.12,jointDepth*.58,thickness*.17);
    const tint=1+(rand()-.5)*.36,warm=(rand()-.5)*.065;
    color.setRGB(tint*(1+warm),tint,tint*(1-warm));
    const start=indices.length,base=positions.length/3,n=poly.length;
    const cuts=poly.map(()=>rand()<.24?rand()*.26:0);
    const edgeWear=poly.map(()=>.88+rand()*.24);
    for(let ring=0;ring<4;ring++)for(let i=0;i<n;i++) {
      const p=poly[i],r=ring===0?.93:ring===1?1:ring===2?.978:1-(pattern==='cobble'?.125:.08)*edgeWear[i]-cuts[i]*.32;
      const x=lerp(center[0],p[0],r),z=lerp(center[1],p[1],r);
      const tilt=(x-center[0])*tx+(z-center[1])*tz;
      const chip=fbm2(x*41,z*41,{seed:seed+11,octaves:2})*Math.min(.0015,thickness*.02);
      const y=ring===0?-thickness:ring===1?Math.min(bedHeight-Math.min(.003,thickness*.06),height-bevel*1.3):
        ring===2?height-bevel*edgeWear[i]+tilt+chip:height+tilt+chip-cuts[i]*bevel*.9;
      vertex([x,y,z],color);
    }
    for(let ring=0;ring<3;ring++)for(let i=0;i<n;i++) {
      const next=(i+1)%n,a=base+ring*n+i,b=base+ring*n+next,c=b+n,d=a+n;
      triangle(a,b,c);triangle(a,c,d);
    }
    const top=vertex([center[0],height+crown,center[1]],color);
    const bottom=vertex([center[0],-thickness,center[1]],color);
    for(let i=0;i<n;i++) {
      const next=(i+1)%n;
      triangle(top,base+3*n+i,base+3*n+next);
      triangle(bottom,base+next,base+i);
    }
    ranges.push([start,indices.length-start]);outlines.push(poly.map(p=>[...p]));
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
  geometry.setIndex(indices);geometry.computeVertexNormals();geometry.computeBoundingBox();geometry.computeBoundingSphere();
  // Three scales bounding spheres by the largest matrix column, which can
  // under-bound shear from nested nonuniform scale and rotation. sqrt(3)
  // bounds the ratio to the matrix spectral norm; exact bounds stay intact.
  geometry.boundingSphere.radius*=Math.sqrt(3);
  const material=surfaceMaterial(opts,bedHeight),stones=new THREE.Mesh(geometry,material);
  stones.name='PavingStones';stones.castShadow=true;stones.receiveShadow=true;
  const bedGeometry=new THREE.BoxGeometry(width,thickness-jointDepth,depth);
  bedGeometry.translate(0,(-thickness+bedHeight)/2,0);
  bedGeometry.computeBoundingSphere();bedGeometry.boundingSphere.radius*=Math.sqrt(3);
  const bedMaterial=new THREE.MeshStandardMaterial({color:opts.jointColor??0x514c40,
    roughness:lerp(.99,.72,clamp(opts.moisture??0,0,1)),metalness:0});
  const bed=new THREE.Mesh(bedGeometry,bedMaterial);bed.name='PavingJointBed';bed.castShadow=true;bed.receiveShadow=true;
  const root=new THREE.Group();root.name=opts.name??'Paving';root.add(bed,stones);
  root.userData.placement=bed.userData.placement=stones.userData.placement='free';

  // Index upward-facing triangles after Float32 conversion. Query exactly
  // what the GPU/raycaster receives, including sloped bevels at joint edges.
  const p=geometry.attributes.position,index=geometry.index.array,bins=new Map();
  const binSize=Math.max(layout.spacing,1e-5),triangles=[];
  for(let i=0;i<index.length;i+=3) {
    const a=index[i],b=index[i+1],c=index[i+2];
    const ax=p.getX(a),az=p.getZ(a),bx=p.getX(b),bz=p.getZ(b),cx=p.getX(c),cz=p.getZ(c);
    const det=(bz-cz)*(ax-cx)+(cx-bx)*(az-cz);
    // CCW in world XZ is a DOWN-facing triangle; skip bottom and verticals.
    if(det>=-1e-13)continue;
    const t={ax,az,bx,bz,cx,cz,ay:p.getY(a),by:p.getY(b),cy:p.getY(c),det};
    const id=triangles.length;triangles.push(t);
    for(let x=Math.floor(Math.min(ax,bx,cx)/binSize);x<=Math.floor(Math.max(ax,bx,cx)/binSize);x++)
    for(let z=Math.floor(Math.min(az,bz,cz)/binSize);z<=Math.floor(Math.max(az,bz,cz)/binSize);z++) {
      const key=x+','+z;if(!bins.has(key))bins.set(key,[]);bins.get(key).push(id);
    }
  }
  root.userData.sampleHeight=(x,z)=>{
    if(!Number.isFinite(x)||!Number.isFinite(z)||Math.abs(x)>width/2||Math.abs(z)>depth/2)return null;
    let height=bedHeight;
    for(const id of bins.get(Math.floor(x/binSize)+','+Math.floor(z/binSize))??[]) {
      const t=triangles[id],a=((t.bz-t.cz)*(x-t.cx)+(t.cx-t.bx)*(z-t.cz))/t.det;
      const b=((t.cz-t.az)*(x-t.cx)+(t.ax-t.cx)*(z-t.cz))/t.det,c=1-a-b;
      if(Math.min(a,b,c)>=-1e-7)height=Math.max(height,a*t.ay+b*t.by+c*t.cy);
    }
    return height;
  };
  root.userData.update=()=>{};
  Object.assign(root.userData,{seed,stoneCount:ranges.length,stoneRanges:ranges,
    stoneOutlines:outlines,size:[width,depth],stoneSize:layout.spacing,joint,relief,bedHeight});
  return attachDisposal(root,snapshotResources(root));
}
