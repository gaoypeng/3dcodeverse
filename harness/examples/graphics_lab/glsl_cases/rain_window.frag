// Rain on glass with coherent falling droplets and a defocused night street.
// The library supplies dropsLayer, bokehSoft, hash12, tonemapACES and gamma.
vec3 street(vec2 uv, float t) {
    vec2 p=(uv-.5)*vec2(u_resolution.x/u_resolution.y,1.0);
    float city=1.0-smoothstep(-.03,.35,p.y);
    vec3 colour=vec3(.005,.008,.015)+bokehSoft(p*1.1+vec2(0,.11),t*.15)*city*1.4;
    float horizon=exp(-pow((p.y+.10)*7.0,2.0));
    colour+=vec3(.015,.025,.034)*horizon;
    float traffic=exp(-pow((p.y+.29)*22.0,2.0));
    colour+=traffic*(vec3(.3,.045,.012)*exp(-pow((p.x-.24-.035*t)*9.0,2.0))+
                      vec3(.12,.20,.32)*exp(-pow((p.x+.3+.045*t)*12.0,2.0)));
    return colour;
}
void mainImage(out vec4 fragColor,in vec2 fragCoord) {
    vec2 uv=fragCoord/u_resolution;
    vec2 rain=dropsLayer(uv,u_time*1.2,3.8)+.35*dropsLayer(uv+3.17,u_time*.9,7.3);
    float bead=rain.x+.3*rain.y;
    vec2 normal=vec2(dFdx(bead),dFdy(bead))*u_resolution*vec2(.0008,.0011);
    normal=clamp(normal,vec2(-.025),vec2(.025));
    vec3 colour=street(uv+normal,u_time);
    colour*=1.0-.14*rain.x;
    float edge=length(normal);
    colour+=street(uv+vec2(-.04,.07),u_time)*.18*smoothstep(.008,.027,edge)*(1.0-.7*rain.y);
    colour*=.82+.18*pow(16.0*uv.x*uv.y*(1.0-uv.x)*(1.0-uv.y),.2);
    colour+=(hash12(fragCoord)-.5)/900.0;
    fragColor=vec4(gamma(tonemapACES(colour)),1.0);
}
