// Prescribed rising density, not a fluid simulation. Metre-space integration.
float plumeDensity(vec3 p,float t) {
    float height=p.y;
    if(height<0.0||height>3.0) return 0.0;
    float age=height/.65;
    vec2 drift=vec2(.08*age,.03*age)+vec2(.10*sin(height*3.0-t*1.1),.09*cos(height*2.7-t*.9))*height;
    vec3 q=vec3(p.xz-drift,height-t*.65);
    vec3 warp=vec3(noise3(q*2.5),noise3(q*2.5+13.0),noise3(q*2.5+29.0))-.5;
    float coarse=fbmVolume(q*3.2+warp*.8+vec3(4.2,1.7,0.0));
    float radius=.12+.21*height;
    float body=max(0.0,1.0-length(p.xz-drift)/radius+(coarse-.47)*2.8);
    float detail=fbmVolume(q*9.7+vec3(13.0,7.0,t*.13));
    return max(0.0,body*.65-(1.0-detail)*.25)*
           smoothstep(0.0,.13,height)*(1.0-smoothstep(2.1,3.0,height));
}
void mainImage(out vec4 fragColor,in vec2 fragCoord) {
    vec2 uv=(fragCoord-.5*u_resolution)/u_resolution.y;
    vec3 ro=vec3(3.0,2.7,5.0),target=vec3(.1,1.3,0);
    vec3 f=normalize(target-ro),right=normalize(cross(f,vec3(0,1,0))),up=cross(right,f);
    vec3 rd=normalize(uv.x*right+uv.y*up+1.55*f);
    vec3 toSun=normalize(vec3(-1.5,2.5,-1.0));
    vec3 horizon=vec3(.06,.085,.11);
    vec3 background=mix(horizon,vec3(.025,.055,.085),smoothstep(0.0,.6,rd.y));
    float ground=rd.y<0.0?-ro.y/rd.y:1000.0;
    if(ground<1000.0) {
        vec3 p=ro+rd*ground;
        background=vec3(.035,.041,.046)*(.92+.08*noise(p.xz*12.0));
        background=mix(background,horizon,1.0-exp(-ground*.06));
        background*=1.0-.5*exp(-dot(p.xz,p.xz)*3.0);
    }
    vec3 inv=1.0/(rd+vec3(1e-7));
    vec3 nearBox=(vec3(-1.4,0,-1.4)-ro)*inv,farBox=(vec3(1.4,3,1.4)-ro)*inv;
    vec3 lower=min(nearBox,farBox),upper=max(nearBox,farBox);
    float enter=max(0.0,max(lower.x,max(lower.y,lower.z))),leave=min(ground,min(upper.x,min(upper.y,upper.z)));
    vec3 colour=vec3(0);float transmission=1.0;
    if(leave>enter) {
        float stepLength=(leave-enter)/64.0;
        for(int i=0;i<64;i++) {
            vec3 p=ro+rd*(enter+(float(i)+.5)*stepLength);
            float density=plumeDensity(p,u_time),lightDepth=0.0;
            if(density>.001) {
                for(int j=0;j<6;j++) lightDepth+=plumeDensity(p+toSun*(float(j)+.5)*.18,u_time)*.18;
                float stepT=beerTransmittance(density*3.6,stepLength);
                vec3 illumination=vec3(.075,.12,.17)+vec3(7.0,5.9,4.4)*
                    phaseHG(dot(rd,toSun),.45)*beerTransmittance(lightDepth*3.6,1.0);
                colour+=transmission*(1.0-stepT)*illumination;
                transmission*=stepT;
            }
            if(transmission<.015) break;
        }
    }
    fragColor=vec4(gamma(tonemapACES(colour+transmission*background)),1.0);
}
