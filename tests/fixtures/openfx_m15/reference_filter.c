/* Original test-only OpenFX v1 image-effect reference. SPDX-License-Identifier: MIT
 * Narrow filter: invert RGB, preserve A; straight byte RGBA bottom-left rows.
 * ABI field layouts match the official OpenFX v1 C API headers. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#include <windows.h>
#else
#define EXPORT __attribute__((visibility("default")))
#include <unistd.h>
#endif

typedef void *Handle;
typedef int Status;
typedef struct {
  Handle host;
  const void *(*fetchSuite)(Handle, const char *, int);
} OfxHost;
typedef struct {
  const char *pluginApi;
  int apiVersion;
  const char *pluginIdentifier;
  unsigned major, minor;
  void (*setHost)(OfxHost *);
  Status (*mainEntry)(const char *, const void *, Handle, Handle);
} OfxPlugin;
typedef struct {
  Status (*setPointer)(Handle,const char*,int,void*);
  Status (*setString)(Handle,const char*,int,const char*);
  Status (*setDouble)(Handle,const char*,int,double);
  Status (*setInt)(Handle,const char*,int,int);
  void *setPointerN;
  void *setStringN;
  void *setDoubleN;
  void *setIntN;
  Status (*getPointer)(Handle,const char*,int,void**);
  Status (*getString)(Handle,const char*,int,char**);
  Status (*getDouble)(Handle,const char*,int,double*);
  Status (*getInt)(Handle,const char*,int,int*);
  void *getPointerN;
  void *getStringN;
  void *getDoubleN;
  Status (*getIntN)(Handle,const char*,int,int*);
  void *reset;
  Status (*dimension)(Handle,const char*,int*);
} PropertySuite;
typedef struct {
  Status (*getPropertySet)(Handle,Handle*);
  Status (*getParamSet)(Handle,Handle*);
  Status (*clipDefine)(Handle,const char*,Handle*);
  Status (*clipGetHandle)(Handle,const char*,Handle*,Handle*);
  Status (*clipGetPropertySet)(Handle,Handle*);
  Status (*clipGetImage)(Handle,double,const void*,Handle*);
  Status (*clipReleaseImage)(Handle);
  void *clipGetRegionOfDefinition;
  void *abort;
  void *imageMemoryAlloc;
  void *imageMemoryFree;
  void *imageMemoryLock;
  void *imageMemoryUnlock;
} ImageSuite;
static const PropertySuite *P = 0;
static const ImageSuite *E = 0;
static OfxHost *H = 0;
static void setHost(OfxHost *host) { H=host; }

static Status mainEntry(const char *action, const void *effect, Handle args, Handle out) {
  (void)out;
  if (!strcmp(action,"OfxActionLoad")) {
    P=(const PropertySuite*)H->fetchSuite(H->host,"OfxPropertySuite",1);
    E=(const ImageSuite*)H->fetchSuite(H->host,"OfxImageEffectSuite",1);
    return P && E ? 0 : 4;
  }
  if (!strcmp(action,"OfxActionDescribe")) {
    Handle props=0;
    if (E->getPropertySet((Handle)effect,&props)) return 1;
    if (P->setString(props,"OfxImageEffectPropSupportedContexts",0,"OfxImageEffectContextFilter"))return 1;
    if (P->setString(props,"OfxImageEffectPropSupportedPixelDepths",0,"OfxBitDepthByte"))return 1;
    P->setInt(props,"OfxImageEffectPropSupportsTiles",0,0);
    P->setInt(props,"OfxImageEffectPropSupportsMultiResolution",0,0);
    return 0;
  }
  if (!strcmp(action,"OfxImageEffectActionDescribeInContext")) {
    Handle a=0,b=0;
    if (E->clipDefine((Handle)effect,"Source",&a))return 1;
    if (E->clipDefine((Handle)effect,"Output",&b))return 1;
    P->setString(a,"OfxImageClipPropSupportedComponents",0,"OfxImageComponentRGBA");
    P->setString(b,"OfxImageClipPropSupportedComponents",0,"OfxImageComponentRGBA");
    return 0;
  }
  if (!strcmp(action,"OfxImageEffectActionRender")) {
#if defined(REF_MODE) && REF_MODE == 1
    return 1;
#elif defined(REF_MODE) && REF_MODE == 2
    abort();
#elif defined(REF_MODE) && REF_MODE == 3
    #if defined(_WIN32)
      Sleep(60000);
    #else
      sleep(60);
    #endif
#endif
    double time=0;
    if (P->getDouble(args,"OfxPropTime",0,&time))return 1;
    Handle source=0,target=0,sImage=0,dImage=0;
    if (E->clipGetHandle((Handle)effect,"Source",&source,0))return 1;
    if (E->clipGetHandle((Handle)effect,"Output",&target,0))return 1;
    if (E->clipGetImage(source,time,0,&sImage))return 1;
    if (E->clipGetImage(target,time,0,&dImage))return 1;
    void *src=0,*dst=0;
    if (P->getPointer(sImage,"OfxImagePropData",0,&src))return 1;
    if (P->getPointer(dImage,"OfxImagePropData",0,&dst))return 1;
    int bounds[4]={0},sRow=0,dRow=0;
    if (P->getIntN(sImage,"OfxImagePropBounds",4,bounds))return 1;
    if (P->getInt(sImage,"OfxImagePropRowBytes",0,&sRow))return 1;
    if (P->getInt(dImage,"OfxImagePropRowBytes",0,&dRow))return 1;
    int w=bounds[2]-bounds[0],h=bounds[3]-bounds[1];
    if (w<=0||h<=0||sRow<w*4||dRow<w*4)return 1;
    for(int y=0;y<h;y++){
      const unsigned char *s=(const unsigned char*)src+(size_t)y*sRow;
      unsigned char *d=(unsigned char*)dst+(size_t)y*dRow;
      for(int x=0;x<w;x++){
        d[x*4]=255-s[x*4];d[x*4+1]=255-s[x*4+1];
        d[x*4+2]=255-s[x*4+2];d[x*4+3]=s[x*4+3];
      }
    }
    E->clipReleaseImage(sImage);
    E->clipReleaseImage(dImage);
    return 0;
  }
  if (!strcmp(action,"OfxActionCreateInstance")||!strcmp(action,"OfxActionDestroyInstance")||
      !strcmp(action,"OfxActionUnload"))return 0;
  return 14;
}
static OfxPlugin plugin={"OfxImageEffectPluginAPI",1,
 "org.framerudiments.reference.invert",1,0,setHost,mainEntry};
EXPORT int OfxGetNumberOfPlugins(void){return 1;}
EXPORT OfxPlugin *OfxGetPlugin(int nth){return nth==0?&plugin:0;}
