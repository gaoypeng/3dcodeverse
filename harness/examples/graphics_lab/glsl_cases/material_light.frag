// Analytic spheres under a studio key: direct GGX and approximate environment fill.
// No transmission, area-light solver, or path-traced indirect light is claimed.
vec3 centre(int i) { return vec3((float(i)-1.0)*1.5,0.0,0.0); }
float sphereHit(vec3 ro,vec3 rd,vec3 c) {
    vec3 oc=ro-c;float b=dot(oc,rd),h=b*b-dot(oc,oc)+.64*.64;
    if(h<0.0) return 1000.0;
    float near=-b-sqrt(h);return near>.001?near:1000.0;
}
vec3 studio(vec3 d,float roughness) {
    vec3 base=mix(vec3(.12,.09,.065),vec3(.10,.15,.23),smoothstep(-.2,.8,d.y));
    float softbox=pow(max(dot(d,normalize(vec3(-1,2,2))),0.0),mix(72.0,4.0,roughness));
    float strip=pow(max(dot(d,normalize(vec3(2,1,-1))),0.0),mix(100.0,8.0,roughness));
    return base+vec3(2.8,2.5,2.0)*softbox+vec3(.4,.8,1.2)*strip;
}
float visibility(vec3 p,vec3 toLight) {
    float result=1.0;
    for(int i=0;i<3;i++) {
        vec3 toSphere=centre(i)-p;float along=dot(toSphere,toLight);
        if(along>.02) {
            float distance=length(toSphere-toLight*along);
            result=min(result,smoothstep(.58,.70+along*.08,distance));
        }
    }
    return result;
}
void mainImage(out vec4 fragColor,in vec2 fragCoord) {
    vec2 uv=(fragCoord-.5*u_resolution)/u_resolution.y;
    vec3 ro=vec3(.65,1.9,5.6),target=vec3(0,-.12,0);
    vec3 f=normalize(target-ro),r=normalize(cross(f,vec3(0,1,0))),up=cross(r,f);
    vec3 rd=normalize(uv.x*r+uv.y*up+1.65*f);
    float hit=1000.0;int object=-1;
    for(int i=0;i<3;i++) {float d=sphereHit(ro,rd,centre(i));if(d<hit){hit=d;object=i;}}
    float ground=rd.y<0.0?(-.64-ro.y)/rd.y:1000.0;
    if(ground<hit){hit=ground;object=3;}
    vec3 colour=studio(rd,1.0)*.45;
    if(hit<1000.0) {
        vec3 p=ro+rd*hit,n=object==3?vec3(0,1,0):normalize(p-centre(object)),v=-rd;
        vec3 albedo=object==0?vec3(.65,.21,.075):object==1?vec3(.72,.51,.20):vec3(.55,.67,.72);
        float metal=object==1?1.0:0.0,roughness=object==0?.5:object==1?.19:.12;
        if(object==3){albedo=vec3(.18,.2,.21)*(.88+.12*noise(p.xz*18.0));roughness=.88;metal=0.0;}
        vec3 light=normalize(vec3(-2.2+sin(u_time*.5),4.2,3.0));
        float shadow=visibility(p+n*.002,light);
        colour=pbrDirect(albedo,metal,roughness,n,v,light,vec3(4.3,3.9,3.3))*shadow;
        colour+=pbrDirect(albedo,metal,roughness,n,v,normalize(vec3(3,1,-2)),vec3(.25,.50,.9));
        vec3 fresnel=fresnelSchlick(max(dot(n,v),0.0),mix(vec3(.04),albedo,metal));
        colour+=(1.0-metal)*(1.0-fresnel)*albedo*studio(n,1.0)*.3;
        colour+=fresnel*studio(reflect(-v,n),roughness)*(1.0-.55*roughness);
        if(object==3) {
            float contact=1.0;
            for(int i=0;i<3;i++) contact*=1.0-.45*exp(-3.5*length(p.xz-centre(i).xz));
            colour*=contact;
        }
        colour=mix(colour,vec3(.08,.1,.13),1.0-exp(-hit*.008));
    }
    fragColor=vec4(gamma(tonemapACES(colour)),1.0);
}
