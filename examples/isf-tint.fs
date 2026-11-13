/*{
  "ISFVSN": "2",
  "DESCRIPTION": "A blue tint with a published amount control",
  "CATEGORIES": ["Color Effect"],
  "INPUTS": [
    {"NAME": "inputImage", "TYPE": "image"},
    {"NAME": "amount", "TYPE": "float", "LABEL": "Tint amount",
     "MIN": 0, "MAX": 1, "DEFAULT": 0.25, "IDENTITY": 0},
    {"NAME": "tint", "TYPE": "color", "DEFAULT": [0.2, 0.6, 1, 1]},
    {"NAME": "enabled", "TYPE": "bool", "DEFAULT": true}
  ]
}*/
void main() {
    vec4 pixel = IMG_THIS_PIXEL(inputImage);
    gl_FragColor = vec4(mix(pixel.rgb, tint.rgb, enabled ? amount : 0.0), pixel.a);
}
