/**
 * Near and middle-distance trees with connected branches and individual leaves.
 * Metres; +Y up; the root stays at local y=0. No textures or network requests.
 *
 * makeTree({species:'oak'|'birch'|'willow',height:6,crownRadius:2.5,seed:7,
 *   leafDensity:1,leafSize:.12,wind:{dir:[1,.3],strength:.5,speed:1},
 *   autumn:0,shadows:true}) -> Group containing Wood and Leaves.
 * makeShrub({...}) uses a low, spreading, multi-stem branch architecture.
 * update(t), tick(t), dispose(), sampleWind(point,t), bounds and branches are
 * on userData. Time is absolute and repeatable; wind uses local XZ axes.
 * The leaf mesh is real instancing, so the host AO pass sees its rest pose.
 * Colour, directional/spot depth and point-distance shadows share deformation.
 * height and crownRadius are nominal architecture dimensions; bounds is the
 * actual local envelope including leaves and maximum wind displacement.
 * leafDensity (0..4) changes leaves per shoot; maxLeaves bounds the count and thins
 * the entire crown without bias toward early branches. Wood remains connected.
 * Each factory merges its wood and its leaves into one geometry each.
 * Default species are heavy geometry. leafSegments (integer
 * 3..32, defaults oak12/birch16/willow10) controls leaf curvature geometry
 * independently of crown density; use 4 when individual leaf lobes are unresolved.
 * Reduce leafDensity/maxLeaves as well for distant trees. These are geometry
 * assets, not far cards; choose the detail when constructing the tree.
 * dispose() releases captured owned resources, including detached original
 * children, and leaves subsequently appended caller resources untouched.
 * There is no collision, fracture or seasonal growth simulation.
 */
import * as THREE from 'three';
import {mulberry32} from './noise.js';
import {windOf} from './grass.js';
import {intOption,option,patchStandard,shadowLike,tickShaders} from './shader.js';
import {patchLeafSSS} from './foliage_shade.js';
import {attachDisposal,snapshotResources} from './lifecycle.js';

const TAU=Math.PI*2;
const SPECIES={
  oak:{radius:.42,trunk:.037,bark:0x665547,leaf:0x49672b,leafSize:.125,width:.65,
    primary:12,secondary:5,twigs:4,droop:.03,crownStart:.29},
  birch:{radius:.28,trunk:.018,bark:0xb8b5a0,leaf:0x587635,leafSize:.085,width:.76,
    primary:14,secondary:4,twigs:4,droop:.08,crownStart:.28},
  willow:{radius:.46,trunk:.029,bark:0x625c45,leaf:0x607742,leafSize:.17,width:.19,
    primary:11,secondary:5,twigs:5,droop:.30,crownStart:.32},
};
const V=(x=0,y=0,z=0)=>new THREE.Vector3(x,y,z);

const WIND_HEAD=/* glsl */`
uniform float uTime;
uniform float uTreeHeight;
uniform float uTreeAmp;
uniform float uTreeSpeed;
uniform float uTreePhase;
uniform vec2 uTreeWind;
#ifdef USE_INSTANCING
attribute vec4 treeLeaf;
varying vec4 vTreeLeaf;
#endif
varying vec3 vTreeRest;
vec3 treeWindOffset(vec3 p) {
  float b=clamp(p.y/uTreeHeight,0.0,1.0), t=uTime*uTreeSpeed;
  float main=(.62*sin(t+uTreePhase)+.28*sin(t*.53+uTreePhase*1.7))
    *(.65+.35*sin(t*.24+uTreePhase));
  float side=.25*sin(t*.81+uTreePhase);
  float twig=.07*sin(p.x*.7+p.z*.43+t*1.7+uTreePhase)*b;
  vec2 d=(uTreeWind*(main+twig)+vec2(-uTreeWind.y,uTreeWind.x)*side)*uTreeAmp*b*b;
  return vec3(d.x,0.0,d.y);
}
mat3 treeWindJacobian(vec3 p) {
  float b=clamp(p.y/uTreeHeight,0.0,1.0),t=uTime*uTreeSpeed;
  float db=p.y>0.0&&p.y<uTreeHeight?1.0/uTreeHeight:0.0;
  float main=(.62*sin(t+uTreePhase)+.28*sin(t*.53+uTreePhase*1.7))
    *(.65+.35*sin(t*.24+uTreePhase));
  vec2 base=uTreeWind*main+vec2(-uTreeWind.y,uTreeWind.x)*(.25*sin(t*.81+uTreePhase));
  float ph=p.x*.7+p.z*.43+t*1.7+uTreePhase;
  vec2 dx=uTreeWind*(uTreeAmp*.07*b*b*b*cos(ph)*.7);
  vec2 dz=uTreeWind*(uTreeAmp*.07*b*b*b*cos(ph)*.43);
  vec2 dy=uTreeAmp*db*(base*2.0*b+uTreeWind*(.21*b*b*sin(ph)));
  return mat3(vec3(1.0+dx.x,0.0,dx.y),vec3(dy.x,1.0,dy.y),vec3(dz.x,0.0,1.0+dz.y));
}
`;
const WIND_BODY=/* glsl */`
  vec3 trP=transformed;
  vec3 trN=normal;
  #ifdef USE_INSTANCING
    float trFlutter=.10*(uTreeAmp/uTreeHeight)*30.0
      *sin(uTime*uTreeSpeed*5.2+treeLeaf.x);
    trP.z+=trFlutter*trP.y*trP.y;
    trN=astraNormalTransform(mat3(vec3(1.,0.,0.),vec3(0.,1.,2.*trFlutter*transformed.y),vec3(0.,0.,1.)),trN);
    trP=(instanceMatrix*vec4(trP,1.0)).xyz;
    trN=astraNormalTransform(mat3(instanceMatrix),trN);
    vTreeLeaf=treeLeaf;
  #endif
  vTreeRest=trP;
  trN=astraNormalTransform(treeWindJacobian(trP),trN);
  vec3 trMoved=trP+treeWindOffset(trP);
  #ifdef USE_INSTANCING
    transformed=(inverse(instanceMatrix)*vec4(trMoved,1.0)).xyz;
  #else
    transformed=trMoved;
  #endif
  #ifndef FLAT_SHADED
    transformedNormal=normalMatrix*trN;
    vNormal=normalize(transformedNormal);
  #endif
`;

function windUniforms(height,wind,seed) {
  return {uTreeHeight:{value:height},uTreeAmp:{value:height*.026*wind.amp},
    uTreeSpeed:{value:wind.speed},uTreePhase:{value:(seed%65536)*.0137},
    uTreeWind:{value:wind.dir.clone()}};
}

function pointOn(branch,t) {return branch.curve.getPoint(THREE.MathUtils.clamp(t,0,1));}
function tangentOn(branch,t) {return branch.curve.getTangent(THREE.MathUtils.clamp(t,0,1)).normalize();}
function radiusAt(branch,u) {
  const taper=branch.order===0?.83:branch.order===1?.90:.95;
  return branch.radius*Math.pow(1-u*taper,branch.order===0?.78:.88);
}

/** Build a bounded connected graph, then clothe its terminal shoots. */
function skeleton(config,random) {
  const {height:h,radius:r,species,spec,shrub}=config;
  const branches=[];
  function branch(parent,at,start,c1,c2,end,radius,order) {
    const result={id:branches.length,parent:parent?.id??null,at,order,radius,
      curve:new THREE.CubicBezierCurve3(start,c1,c2,end)};
    branches.push(result);return result;
  }
  const stems=shrub?5:1;
  for(let stem=0;stem<stems;stem++) {
    const theta=stem*2.39996+random();
    const root=shrub?V(Math.cos(theta)*r*.055,0,Math.sin(theta)*r*.055):V();
    const top=V((random()-.5)*h*.16,h*(shrub?.72:species==='oak'?.88:.92),(random()-.5)*h*.16);
    if(shrub) {top.x+=Math.cos(theta)*r*.38;top.z+=Math.sin(theta)*r*.38;}
    const trunkR=h*spec.trunk*(shrub?.45:1)*( .9+random()*.2);
    const trunk=branch(null,0,root,root.clone().add(V((random()-.5)*h*.07,h*.34,(random()-.5)*h*.07)),
      top.clone().multiplyScalar(.66).add(V((random()-.5)*h*.10,h*.04,(random()-.5)*h*.10)),top,trunkR,0);
    // Low buttresses spread the load into the ground. Their ends tuck below
    // grade, leaving the exposed root shoulder continuous with the trunk.
    if(!shrub)for(let j=0;j<6;j++) {
      const a=j*TAU/6+random()*.50,d=V(Math.cos(a),0,Math.sin(a));
      const start=pointOn(trunk,.022),length=trunkR*(2.8+random()*1.9);
      const end=root.clone().addScaledVector(d,length);end.y=-trunkR*.40;
      branch(trunk,.022,start,start.clone().addScaledVector(d,length*.20).add(V(0,-trunkR*.1,0)),
        end.clone().addScaledVector(d,-length*.35).add(V(0,trunkR*.12,0)),end,trunkR*(.38+random()*.17),-1);
    }
    const primaries=shrub?4:spec.primary;
    for(let i=0;i<primaries;i++) {
      const at=spec.crownStart+(1-spec.crownStart)*(.02+.89*(i+.30*(random()-.5))/Math.max(1,primaries-1));
      const start=pointOn(trunk,at),angle=i*2.39996+theta+(random()-.5)*.42;
      const envelope=Math.sqrt(Math.max(.08,1-Math.pow((at-.43)/.68,2)));
      let radial=r*envelope*(.55+random()*.50)*(shrub?.55:1);
      const crownSpread=species==='oak'&&!shrub?1-.65*THREE.MathUtils.smoothstep(at,.50,.96):1;
      radial*=crownSpread;
      const dir=V(Math.cos(angle),0,Math.sin(angle));
      const end=start.clone().addScaledVector(dir,radial).add(V(0,h*(.14+.10*(1-at)),0));
      if(species==='oak'&&!shrub)end.y=h*(.48+.50*(at-.29)/.71+(random()-.5)*.12);
      const sideways=V(-dir.z,0,dir.x),crook=(random()-.5)*radial*.70;
      const primaryRadius=radiusAt(trunk,at)*(.44+random()*.31);
      const primary=branch(trunk,at,start,
        start.clone().addScaledVector(tangentOn(trunk,at),radial*(.15+random()*.22))
          .addScaledVector(dir,radial*.19).addScaledVector(sideways,crook),
        end.clone().addScaledVector(dir,-radial*(.19+random()*.18))
          .addScaledVector(sideways,-crook*.65).add(V(0,h*(random()-.40)*.12,0)),end,primaryRadius,1);
      const secondaryCount=shrub?3:spec.secondary;
      for(let j=0;j<secondaryCount;j++) {
        const a=.23+.72*(j+.20*(random()-.5))/Math.max(1,secondaryCount-1),p=pointOn(primary,a);
        const sign=j%2?1:-1,phi=angle+sign*(.30+random()*.85);
        const len=r*(.14+random()*.25)*(shrub?.58:1)*crownSpread,d=V(Math.cos(phi),0,Math.sin(phi));
        const q=p.clone().addScaledVector(d,len).add(V(0,h*(.015+random()*.08-spec.droop*.20),0));
        const second=branch(primary,a,p,p.clone().addScaledVector(tangentOn(primary,a),len*.26),
          q.clone().addScaledVector(d,-len*.27).addScaledVector(sideways,(random()-.5)*len*.40).add(V(0,len*.22,0)),q,
          radiusAt(primary,a)*(.42+random()*.28),2);
        const twigCount=shrub?3:spec.twigs;
        for(let k=0;k<twigCount;k++) {
          const u=.24+.75*k/Math.max(1,twigCount-1),b=pointOn(second,u);
          const ta=phi+(k%2?1:-1)*(.35+random()*.65);
          const tl=r*(.15+random()*.10)*(shrub?.60:1)*crownSpread,td=V(Math.cos(ta),0,Math.sin(ta));
          const down=species==='willow'?h*(.16+random()*.19):h*spec.droop*(.5+random());
          const e=b.clone().addScaledVector(td,tl).add(V(0,h*(.015+random()*.07)-down,0));
          branch(second,u,b,b.clone().addScaledVector(tangentOn(second,u),tl*.35),
            e.clone().addScaledVector(td,-tl*.25).add(V(0,down*.50+tl*.1,0)),e,
            Math.max(.0011,radiusAt(second,u)*(.48+random()*.22)),3);
        }
      }
    }
  }
  // Fine terminal shoots carry the foliage. Their roots lie on real parent
  // curves; phyllotactic rotations distribute foliage around each shoot.
  const twigs=branches.filter(b=>b.order===3);
  for(const twig of twigs)for(let shoot=0;shoot<3;shoot++) {
    const at=.30+shoot*.31,p=pointOn(twig,at),t=tangentOn(twig,at);
    const side=new THREE.Vector3().crossVectors(t,V(0,1,0));
    if(side.lengthSq()<1e-6)side.set(1,0,0);side.normalize();
    const length=r*(shrub?.095:.12)*(.7+random()*.6);
    const around=new THREE.Vector3().crossVectors(t,side).normalize();
    const angle=shoot*2.39996+twig.id*.71+random()*.6;
    const d=t.clone().multiplyScalar(.42).addScaledVector(side,Math.cos(angle)*.85)
      .addScaledVector(around,Math.sin(angle)*.85);
    d.y+=species==='willow'?-.40:.12+random()*.28;d.normalize();
    const end=p.clone().addScaledVector(d,length);
    branch(twig,at,p,p.clone().addScaledVector(t,length*.3),
      end.clone().addScaledVector(d,-length*.24).add(V(0,length*.14,0)),end,
      Math.max(.00065,radiusAt(twig,at)*(.48+random()*.18)),4);
  }
  return branches;
}

/** Append a tapered tube using a transported frame; no meshes per branch. */
function tube(data,points,radii,sides,phase=0) {
  const start=data.positions.length/3;
  let side=null,along=0;
  for(let i=0;i<points.length;i++) {
    if(i)along+=points[i].distanceTo(points[i-1]);
    const p=points[i],tangent=points[Math.min(i+1,points.length-1)].clone()
      .sub(points[Math.max(i-1,0)]).normalize();
    if(!side)side=new THREE.Vector3().crossVectors(tangent,Math.abs(tangent.y)>.92?V(1,0,0):V(0,1,0)).normalize();
    side.addScaledVector(tangent,-side.dot(tangent)).normalize();
    const other=new THREE.Vector3().crossVectors(tangent,side).normalize();
    for(let j=0;j<=sides;j++) {
      const angle=TAU*j/sides,rad=radii[i]*(1+.045*Math.sin(angle*3+phase+i*.31));
      const normal=side.clone().multiplyScalar(Math.cos(angle)).addScaledVector(other,Math.sin(angle));
      const v=p.clone().addScaledVector(normal,rad);
      const before=Math.max(0,i-1),after=Math.min(points.length-1,i+1);
      const slope=(radii[after]-radii[before])/Math.max(points[after].distanceTo(points[before]),1e-8);
      const surfaceNormal=normal.clone().addScaledVector(tangent,-slope).normalize();
      data.positions.push(v.x,v.y,v.z);data.normals.push(surfaceNormal.x,surfaceNormal.y,surfaceNormal.z);
      data.uv.push(j/sides*TAU*radii[0]+phase,along);
      if(i<points.length-1&&j<sides) {
        const a=start+i*(sides+1)+j,b=a+sides+1;
        data.indices.push(a,a+1,b,a+1,b+1,b);
      }
    }
  }
  // End cap prevents exposed terminal shoots from showing an open tube.
  const center=data.positions.length/3,p=points.at(-1);
  const direction=p.clone().sub(points.at(-2)).normalize();
  data.positions.push(p.x,p.y,p.z);data.normals.push(...direction.toArray());data.uv.push(.5,1);
  const ring=start+(points.length-1)*(sides+1);
  for(let j=0;j<sides;j++)data.indices.push(center,ring+j,ring+j+1);
}

function bladeGeometry(species,rows) {
  const positions=[],uv=[],indices=[];
  for(let j=0;j<=rows;j++)for(let k=0;k<3;k++) {
    const u=j/rows,x=k-1;
    let width=Math.pow(Math.max(Math.sin(Math.PI*u),0),.70);
    if(species==='oak')width*=.82+.18*Math.cos(u*TAU*5.2);
    if(species==='birch')width*=1.15-.50*u+(j%2?-.08:.04);
    if(species==='willow')width*=.88+.12*Math.sin(Math.PI*u);
    positions.push(x*width*.5,u,.055*Math.sin(Math.PI*u)*(1-x*x)-.10*u*u);
    uv.push(k/2,u);
    if(j<rows&&k<2) {const a=j*3+k;indices.push(a,a+1,a+3,a+1,a+4,a+3);}
  }
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeVertexNormals();
  return geometry;
}

function leafPlacements(branches,config,random,wood) {
  const leaves=[],{spec,height:h,leafSize,leafDensity,maxLeaves,autumn,species}=config;
  const countFor=branch=>branch.order<2?0:Math.max(0,Math.round((branch.order===4?20:branch.order===3?4:3)*leafDensity));
  let remaining=branches.reduce((sum,branch)=>sum+countFor(branch),0);
  let keep=Math.min(maxLeaves,remaining);
  for(const branch of branches) {
    const count=countFor(branch);
    for(let i=0;i<count;i++) {
      // Exact-size sampling without replacement thins the whole crown evenly.
      // A cap must not strip the late-created side or terminal shoots bare.
      const accepted=random()<keep/Math.max(remaining,1);remaining--;
      if(!accepted)continue;keep--;
      const u=.12+.87*(i+.25+random()*.5)/Math.max(count,1),point=pointOn(branch,u);
      const tangent=tangentOn(branch,u),angle=i*2.39996+branch.id*.83;
      let side=new THREE.Vector3().crossVectors(tangent,V(0,1,0));
      if(side.lengthSq()<1e-5)side=V(1,0,0);side.normalize();
      const normal=new THREE.Vector3().crossVectors(side,tangent).normalize();
      const radial=side.clone().multiplyScalar(Math.cos(angle)).addScaledVector(normal,Math.sin(angle));
      const direction=tangent.clone().multiplyScalar(.48).addScaledVector(radial,.85);
      direction.y+=species==='willow'?-.35:.25;direction.normalize();
      const length=leafSize*(.70+random()*.55)*(1-.20*u),width=length*spec.width*(.84+random()*.22);
      const petiole=length*.13;
      const base=point.clone().addScaledVector(direction,petiole);
      tube(wood,[point,base],[Math.min(.0015,length*.009),Math.min(.0009,length*.005)],3,angle);
      let up=V(0,1,0).addScaledVector(radial,.35).normalize();
      let across=new THREE.Vector3().crossVectors(direction,up);
      if(across.lengthSq()<1e-6)across=side.clone();across.normalize();
      up.crossVectors(across,direction).normalize();
      const matrix=new THREE.Matrix4().makeBasis(across,direction,up);
      matrix.scale(V(width,length,length));matrix.setPosition(base);
      const age=THREE.MathUtils.clamp(random()*.55+autumn*.8,0,1);
      const color=new THREE.Color(config.leafColor).multiplyScalar(.83+random()*.30);
      color.lerp(new THREE.Color(age>.85?0x895129:0xbaa34a),autumn*(.25+random()*.75));
      leaves.push({matrix,color,seed:random()*TAU,age,vigor:1-u*.6,branch:branch.id,at:u,anchor:point});
    }
  }
  return leaves;
}

function woodMaterial(config,uniforms) {
  const material=new THREE.MeshStandardMaterial({color:config.barkColor,roughness:.92,name:'TreeBark'});
  material.defines={USE_UV:''};
  patchStandard(material,{name:'tree:wood',uniforms:{...uniforms,
    uTreeBirch:{value:config.species==='birch'?1:0},uTreeWillow:{value:config.species==='willow'?1:0}},
    vertexHead:WIND_HEAD,vertexBody:WIND_BODY,
    fragmentHead:/* glsl */`
      varying vec3 vTreeRest; uniform float uTreeBirch,uTreeWillow;
      float treeBarkResolved(vec2 frequency,vec2 footprint) {
        return 1.0-smoothstep(.28,.90,max(frequency.x*footprint.x,frequency.y*footprint.y));
      }
      float treeBarkHeight(vec2 p,vec2 footprint) {
        // Branch coordinates are metres around the stem and along its curve.
        // Slowly wandering grain joins along the branch instead of closing into
        // equal-height Voronoi tiles. Willow carries a finer, fibrous grain.
        vec2 frequency=mix(vec2(21.0,5.2),vec2(38.0,3.8),uTreeWillow);
        float wander=astraNoise2(p*vec2(3.3,4.2))*1.3
          +astraNoise2(p*vec2(8.1,1.53)+17.2)*.65;
        vec2 q=p*frequency+vec2(wander,0.0);
        float coarse=astraNoise2(q);
        float ridge=smoothstep(.25,.52,coarse);
        float resolved=treeBarkResolved(frequency,footprint);
        ridge=mix(.51,ridge,resolved);
        vec2 fineFrequency=frequency*vec2(1.47,4.90);
        float fine=astraNoise2(p*fineFrequency+vec2(wander*.8,21.3));
        fine=mix(.5,smoothstep(.26,.68,fine),treeBarkResolved(fineFrequency,footprint));
        vec2 grainFrequency=vec2(170.0,105.0);
        float grain=astraNoise2(p*grainFrequency+37.1);
        grain=mix(.5,grain,treeBarkResolved(grainFrequency,footprint));
        return (ridge*.0024+fine*.0018+grain*.0006)*(1.0-uTreeBirch*.94);
      }
      float treeLenticelAt(vec2 p) {
        return smoothstep(.60,.77,astraNoise2(p*vec2(7.8,170.0)+11.7))
          *smoothstep(.32,.59,astraNoise2(p*vec2(7.0,23.0)+43.2));
      }
      float treeLenticel(vec2 p,vec2 footprint) {
        // Area-sample the narrow horizontal marks before they become subpixel.
        // A simple amplitude fade loses their pigment and causes distance pops.
        float marks=0.0;
        for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++)
          marks+=treeLenticelAt(p+vec2(float(x),float(y))*footprint*.333333);
        float mean=.10*smoothstep(.32,.59,astraNoise2(p*vec2(7.0,23.0)+43.2));
        return mix(marks/9.0,mean,smoothstep(2.0,4.0,max(footprint.x*7.8,footprint.y*170.0)));
      }
    `,
    fragmentBody:/* glsl */`
      // All offset height evaluations use this SAME footprint. Differentiating
      // a per-tap screen filter would turn its quad boundaries into fake relief.
      vec2 trFootprint=max(abs(dFdx(vUv)),abs(dFdy(vUv)));
      float trFurrow=treeBarkHeight(vUv,trFootprint)/(1.0-uTreeBirch*.94);
      diffuseColor.rgb*=mix(.65+.43*smoothstep(.0007,.0047,trFurrow),
        .90+.10*astraNoise2(vUv*vec2(9.,18.)),uTreeBirch);
      if(uTreeBirch>.5) diffuseColor.rgb*=1.0-.76*treeLenticel(vUv,trFootprint);
      diffuseColor.rgb*=mix(.65,1.0,smoothstep(0.0,.35,vTreeRest.y));
    `,
    normalBody:/* glsl */`
      vec2 trE=max(vec2(.0006),trFootprint*.55);
      vec2 trHeightGradient=vec2(
        (treeBarkHeight(vUv+vec2(trE.x,0.),trFootprint)-treeBarkHeight(vUv-vec2(trE.x,0.),trFootprint))/(2.*trE.x),
        (treeBarkHeight(vUv+vec2(0.,trE.y),trFootprint)-treeBarkHeight(vUv-vec2(0.,trE.y),trFootprint))/(2.*trE.y));
      vec2 trScreenGradient=vec2(dot(trHeightGradient,dFdx(vUv)),dot(trHeightGradient,dFdy(vUv)));
      vec3 trSigmaX=dFdx(-vViewPosition),trSigmaY=dFdy(-vViewPosition);
      vec3 trR1=cross(trSigmaY,normal),trR2=cross(normal,trSigmaX);
      float trDet=dot(trSigmaX,trR1);
      if(abs(trDet)>1e-12) {
        vec3 trGradient=(trScreenGradient.x*trR1+trScreenGradient.y*trR2)/trDet;
        // Keep grazing fragments finite without normalizing tiny determinant-
        // scaled vectors. Relief slopes above this limit are unresolved here.
        trGradient*=min(1.0,1.5/max(length(trGradient),1e-8));
        normal=normalize(normal-trGradient);
      }
    `});
  return material;
}

function leafMaterial(config,uniforms) {
  const material=new THREE.MeshPhysicalMaterial({color:0xffffff,roughness:.72,specularIntensity:.35,
    side:THREE.DoubleSide,name:'TreeLeaf'});
  patchStandard(material,{name:'tree:leaf',uniforms,vertexHead:WIND_HEAD,vertexBody:WIND_BODY,
    fragmentHead:'varying vec3 vTreeRest; varying vec4 vTreeLeaf;',
    fragmentBody:/* glsl */`
      float trVein=1.0-smoothstep(.012,.038,abs(vUv.x-.5));
      float trSecondary=sin(vUv.y*60.0-abs(vUv.x-.5)*18.0);
      diffuseColor.rgb*=.95+.025*trSecondary-.075*trVein;
      diffuseColor.rgb*=mix(.87,1.10,vTreeLeaf.z);
      float trScar=astraNoise2(vUv*vec2(35.,52.)+vTreeLeaf.x*5.);
      diffuseColor.rgb*=1.0-smoothstep(.66,.80,trScar)*vTreeLeaf.y*.25;
    `});
  // Explicit UV use is independent of texture presence.
  material.defines={...(material.defines||{}),USE_UV:''};
  patchLeafSSS(material,{strength:.62,power:2.3,tint:0xaec371,variance:.14,varyScale:.8});
  return material;
}

function build(opts,shrub) {
  const species=opts.species===undefined?'oak':opts.species;
  if(!Object.hasOwn(SPECIES,species))throw new RangeError('tree: species must be oak, birch or willow');
  const spec=SPECIES[species],height=option(opts.height, shrub?2.2:6, 'tree: height', .3, 35);
  const leafSegments=intOption(opts.leafSegments, species==='birch'?16:species==='willow'?10:12, 'tree: leafSegments', 3, 32);
  const config={species,spec,height,shrub,seed:option(opts.seed, 7, 'tree: seed'),
    radius:option(opts.crownRadius, height*(shrub?.58:spec.radius), 'tree: crownRadius', .1, 25),
    leafDensity:option(opts.leafDensity, 1, 'tree: leafDensity', 0, 4),
    leafSize:option(opts.leafSize, spec.leafSize, 'tree: leafSize', .025, .5),
    maxLeaves:Math.floor(option(opts.maxLeaves, 24000, 'tree: maxLeaves', 0, 100000)),
    autumn:option(opts.autumn, 0, 'tree: autumn', 0, 1),
    barkColor:opts.barkColor??spec.bark,leafColor:opts.leafColor??spec.leaf};
  const wind=windOf(opts.wind),random=mulberry32(config.seed);
  if(![wind.amp,wind.speed,wind.dir.x,wind.dir.y].every(Number.isFinite))throw new RangeError('tree: wind must be finite');
  const branches=skeleton(config,random),wood={positions:[],normals:[],uv:[],indices:[]};
  for(const branch of branches) {
    const steps=branch.order===0?28:branch.order===1?18:branch.order===2?10:6;
    const points=[],radii=[];
    for(let i=0;i<=steps;i++) {
      const u=i/steps;points.push(pointOn(branch,u));
      let radius=radiusAt(branch,u);
      if(branch.order===0)radius*=1+.95*Math.exp(-u*24);
      else if(branch.order>0)radius*=1+.48*Math.exp(-u*22)+.17*Math.exp(-Math.pow((u-.085)*18,2));
      radii.push(Math.max(radius,.0005));
    }
    tube(wood,points,radii,branch.order===0?18:branch.order===1?12:5,branch.id*.7);
  }
  const leaves=leafPlacements(branches,config,random,wood);
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(wood.positions,3));
  geometry.setAttribute('normal',new THREE.Float32BufferAttribute(wood.normals,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(wood.uv,2));
  geometry.setIndex(wood.indices);geometry.computeBoundingBox();
  const group=new THREE.Group();group.name=opts.name||(shrub?'Shrub':'Tree');
  const uniforms=windUniforms(height,wind,config.seed),bark=woodMaterial(config,uniforms);
  const woodMesh=new THREE.Mesh(geometry,bark);woodMesh.name='Wood';woodMesh.receiveShadow=true;group.add(woodMesh);
  if(opts.shadows!==false)shadowLike(woodMesh,'tree:woodShadow',WIND_HEAD,WIND_BODY);
  let leafMesh=null;
  const bounds=geometry.boundingBox.clone();
  if(leaves.length) {
    const leafGeometry=bladeGeometry(species,leafSegments),material=leafMaterial(config,uniforms);
    const data=new Float32Array(leaves.length*4);
    leafMesh=new THREE.InstancedMesh(leafGeometry,material,leaves.length);leafMesh.name='Leaves';leafMesh.receiveShadow=true;
    for(let i=0;i<leaves.length;i++) {
      const leaf=leaves[i];leafMesh.setMatrixAt(i,leaf.matrix);leafMesh.setColorAt(i,leaf.color);
      data.set([leaf.seed,leaf.age,leaf.vigor,leaf.branch],i*4);
    }
    leafGeometry.setAttribute('treeLeaf',new THREE.InstancedBufferAttribute(data,4));
    leafMesh.instanceMatrix.needsUpdate=true;leafMesh.instanceColor.needsUpdate=true;
    leafMesh.computeBoundingBox();leafMesh.computeBoundingSphere();bounds.union(leafMesh.boundingBox);group.add(leafMesh);
    if(opts.shadows!==false)shadowLike(leafMesh,'tree:leafShadow',WIND_HEAD,WIND_BODY);
  }
  // The formula's horizontal excursion is <=1.22*amp; flutter adds at most
  // .078*wind.amp*leaf length, with a conservative allowance for curved blades.
  const travel=Math.abs(uniforms.uTreeAmp.value)*1.25+config.leafSize*Math.abs(wind.amp)*.12;
  bounds.expandByScalar(travel);
  geometry.boundingBox=geometry.boundingBox.clone().expandByScalar(travel);
  geometry.boundingSphere=geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
  if(leafMesh) {leafMesh.boundingBox.expandByScalar(travel);leafMesh.boundingSphere=leafMesh.boundingBox.getBoundingSphere(new THREE.Sphere());}
  group.userData.bounds=bounds;
  group.userData.species=species;
  group.userData.branchCount=branches.length;
  group.userData.leafCount=leaves.length;
  group.userData.branches=branches.map(branch=>({id:branch.id,parent:branch.parent,at:branch.at,order:branch.order,
    radius:branch.radius,start:branch.curve.v0.clone(),end:branch.curve.v3.clone(),
    sample:(u)=>pointOn(branch,option(u, 0, 'tree: branch parameter', 0, 1))}));
  group.userData.leafAttachments=leaves.map(leaf=>({branch:leaf.branch,at:leaf.at,anchor:leaf.anchor.clone()}));
  group.userData.sampleWind=(point,t=0)=>{
    option(t, 0, 'tree: time', -1e9, 1e9);const p=point?.isVector3?point.clone():Array.isArray(point)&&point.length===3?V(...point):null;
    if(!p||![p.x,p.y,p.z].every(Number.isFinite))throw new RangeError('tree: sampleWind needs a finite local Vector3 or [x,y,z]');
    const u=uniforms,b=THREE.MathUtils.clamp(p.y/height,0,1),time=t*wind.speed,phase=u.uTreePhase.value;
    const main=(.62*Math.sin(time+phase)+.28*Math.sin(time*.53+phase*1.7))*(.65+.35*Math.sin(time*.24+phase));
    const side=.25*Math.sin(time*.81+phase),twig=.07*Math.sin(p.x*.7+p.z*.43+time*1.7+phase)*b;
    const amp=u.uTreeAmp.value*b*b;
    p.x+=(wind.dir.x*(main+twig)-wind.dir.y*side)*amp;
    p.z+=(wind.dir.y*(main+twig)+wind.dir.x*side)*amp;
    return p;
  };
  group.userData.update=group.userData.tick=(t=0)=>{option(t, 0, 'tree: time', -1e9, 1e9);tickShaders(group,t);};
  return attachDisposal(group,snapshotResources(group));
}

export function makeTree(opts={}) {return build(opts,false);}
export function makeShrub(opts={}) {return build(opts,true);}
