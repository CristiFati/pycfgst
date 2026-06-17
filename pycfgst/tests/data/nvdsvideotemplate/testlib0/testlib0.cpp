#include <cstdio>
#include <cstring>
#include <string>

#include <cuda_runtime.h>  // @TODO: Should be in nvdscustomlib_interface.hpp

#include <nvbufsurface.h>  // @TODO: Should be in nvdscustomlib_base.hpp
#include <nvdscustomlib_base.hpp>

#define PROPERTYNAME_PROP0 "prop0"
#define PROPERTYNAME_PROP1 "prop1"
#define PROPERTYNAME_PROP2 "prop2"


class Custom0 : public DSCustomLibraryBase
{
public:
    Custom0() = default;
    virtual ~Custom0() = default;

    bool SetProperty(Property &prop) override
    {
        if ((prop.key == PROPERTYNAME_PROP0)
                || (prop.key == PROPERTYNAME_PROP1)
                || (prop.key == PROPERTYNAME_PROP2)) {
            printf("------- Custom0::SetProperty: [%s] = [%s]\n",
                prop.key.c_str(), prop.value.c_str());
        } else {
            printf("Custom0::SetProperty: Unknown property: %s\n", prop.key.c_str());
            return false;
        }
        return true;
    }

    bool HandleEvent(GstEvent *event) override
    {
        return true;
    }

    char* QueryProperties() override
    {
        printf("Custom0::QueryProperties\n");
        char txt[]{"Custom0"};
        char *ret = new char[sizeof(txt)];
        strcpy(ret, txt);
        return ret;
    }

    GstCaps* GetCompatibleCaps(GstPadDirection direction,
        GstCaps *inCaps, GstCaps *otherCaps) override
    {
        return gst_caps_copy(inCaps);
    }

    BufferResult ProcessBuffer(GstBuffer *inBuf) override
    {
        return BufferResult::Buffer_Ok;
    }
};


extern "C" IDSCustomLibrary* CreateCustomAlgoCtx(DSCustom_CreateParams *params)
{
    return new Custom0();
}
